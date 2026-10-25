"""HTTP client for the Bilibili web API.

The client owns request headers, throttling, WBI key caching and the small
amount of response normalisation the provider needs. It never constructs MA
models and never opens its own aiohttp session: ``mass.http_session`` is
shared by every provider.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from http.cookies import CookieError, SimpleCookie
from typing import Any, Final
from urllib.parse import quote

import aiohttp
from music_assistant.helpers.throttle_retry import Throttler
from music_assistant_models.errors import ResourceTemporarilyUnavailable
from yarl import URL

from . import constants
from .auth import BilibiliCredential
from .wbi import sign_params

_LOGGER: Final = logging.getLogger(__name__)
_REQUEST_TIMEOUT: Final = aiohttp.ClientTimeout(total=10)
_NETWORK_BACKOFF: Final = 5
_SERVER_BACKOFF: Final = 30

_SEARCH_PATH: Final = "/x/web-interface/wbi/search/type"
_NAV_PATH: Final = "/x/web-interface/nav"


class BiliApiError(Exception):
    """A Bilibili API returned a non-zero ``code``."""

    def __init__(self, code: int, message: str, endpoint: str) -> None:
        """Initialize the error with the API code, message and endpoint path."""
        self.code = int(code)
        self.message = str(message)
        self.endpoint = endpoint
        super().__init__(f"Bilibili API error {self.code} at {endpoint}: {self.message}")


@dataclass(slots=True)
class AudioStream:
    """Normalised audio stream description (raw player payload, not an MA model)."""

    stream_id: int
    bandwidth: int
    mime_type: str
    codec: str
    url: str
    backups: list[str] = field(default_factory=list)


def _coerce_int(value: object, default: int = 0) -> int:
    """Best effort int conversion for JSON numbers which arrive as strings."""
    try:
        return int(value)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return default


def _message(payload: Mapping[str, Any]) -> str:
    """Extract a human readable message from an API payload."""
    message = payload.get("message") or payload.get("msg") or ""
    return str(message)


def _data(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the ``data`` object of a payload, or an empty dict."""
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _items(payload: Mapping[str, Any], *keys: str) -> list[dict[str, Any]]:
    """Return the first list of dicts found under ``data``."""
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _response_cookies(response: Any) -> dict[str, str]:
    """Collect cookies set by a response (aiohttp cookies or Set-Cookie headers)."""
    cookies: dict[str, str] = {}
    raw_cookies = getattr(response, "cookies", None)
    if raw_cookies:
        try:
            items = raw_cookies.items()
        except AttributeError:
            items = ()
        for name, value in items:
            actual = getattr(value, "value", value)
            if actual is not None:
                cookies[str(name)] = str(actual)

    headers = getattr(response, "headers", None)
    getall = getattr(headers, "getall", None)
    if not callable(getall):
        return cookies
    jar = SimpleCookie()
    for header in getall("Set-Cookie", []):
        try:
            jar.load(header)
        except CookieError:
            continue
    for name, morsel in jar.items():
        # aiohttp's jar wins for keys it already provided
        cookies.setdefault(str(name), morsel.value)
    return cookies


def _extract_key(url: str) -> str:
    """Extract the WBI key (filename without extension) from an image URL."""
    filename = str(url or "").rsplit("/", 1)[-1].split("?", 1)[0]
    return filename.split(".", 1)[0]


async def _read_json(response: Any) -> Any:
    """Read JSON without raising on a non-JSON body (e.g. an HTML 412 page)."""
    try:
        return await response.json(content_type=None)
    except ValueError, TypeError, aiohttp.ContentTypeError:
        return None


class BilibiliClient:
    """Thin async wrapper around the read-only Bilibili web API."""

    def __init__(
        self,
        mass: Any,
        credential: BilibiliCredential,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the client.

        The aiohttp session is owned by Music Assistant and reused here, so
        :meth:`close` intentionally does nothing.
        """
        self.mass = mass
        self.credential = credential
        self.logger = logger or _LOGGER
        self._api_throttler = Throttler(constants.API_RATE_LIMIT, constants.API_RATE_PERIOD)
        self._search_throttler = Throttler(
            constants.SEARCH_RATE_LIMIT, constants.SEARCH_RATE_PERIOD
        )
        self._wbi_lock = asyncio.Lock()
        self._wbi_img_key = ""
        self._wbi_sub_key = ""
        self._wbi_expires_at = 0.0

    # ------------------------------------------------------------------
    # infrastructure
    # ------------------------------------------------------------------
    async def close(self) -> None:
        """Do nothing: ``mass.http_session`` is shared and outlives the provider."""

    def _headers(self) -> dict[str, str]:
        """Build request headers, adding the credential cookie when logged in."""
        headers = {
            "Referer": constants.BILI_REFERER,
            "Origin": constants.BILI_REFERER,
            "User-Agent": constants.USER_AGENT,
            "Accept": "application/json, text/plain, */*",
        }
        cookie = self.credential.cookie_header()
        if cookie:
            headers["Cookie"] = cookie
        return headers

    def _build_url(
        self,
        base: str,
        path: str,
        params: Mapping[str, object] | None = None,
        *,
        query: str | None = None,
    ) -> URL:
        """Build an already-encoded URL.

        ``encoded=True`` is essential for WBI requests: the signed query must be
        sent byte for byte and must not be encoded a second time by yarl.
        """
        if query is None:
            query = "&".join(
                f"{quote(str(key), safe='')}={quote(str(value), safe='')}"
                for key, value in (params or {}).items()
            )
        url = f"{base}{path}"
        if query:
            url = f"{url}?{query}"
        return URL(url, encoded=True)

    async def _fetch(self, url: URL, endpoint: str) -> tuple[dict[str, Any], dict[str, str]]:
        """Perform one GET request and return the decoded payload and cookies."""
        # Never log the full URL: it contains search keywords and signatures.
        self.logger.debug("GET %s", endpoint)
        try:
            async with self.mass.http_session.get(
                url, headers=self._headers(), timeout=_REQUEST_TIMEOUT
            ) as response:
                status = int(getattr(response, "status", 200))
                cookies = _response_cookies(response)
                payload = await _read_json(response)
        except TimeoutError as err:
            raise ResourceTemporarilyUnavailable(
                f"Timeout while requesting {endpoint}", backoff_time=_NETWORK_BACKOFF
            ) from err
        except aiohttp.ClientError as err:
            raise ResourceTemporarilyUnavailable(
                f"Connection error while requesting {endpoint}", backoff_time=_NETWORK_BACKOFF
            ) from err

        if status >= 500:
            raise ResourceTemporarilyUnavailable(
                f"Bilibili server error {status} at {endpoint}",
                backoff_time=_SERVER_BACKOFF,
            )
        if status == 429:
            raise ResourceTemporarilyUnavailable(
                f"Bilibili rate limited the request at {endpoint}",
                backoff_time=_SERVER_BACKOFF,
            )
        if status == 412:
            # The risk-control response is usually HTML, but the provider maps
            # this code to a longer backoff, so keep it in the API error shape.
            raise BiliApiError(-412, "request was rejected", endpoint)
        if not isinstance(payload, dict):
            if status >= 400:
                raise BiliApiError(-status, f"HTTP {status}", endpoint)
            raise BiliApiError(-1, "invalid JSON response", endpoint)
        if status >= 400:
            code = _coerce_int(payload.get("code"), -status)
            raise BiliApiError(code, _message(payload) or f"HTTP {status}", endpoint)
        return payload, cookies

    async def _request(
        self,
        url: URL,
        endpoint: str,
        *,
        allowed_codes: frozenset[int] = frozenset(),
        extra_throttler: Throttler | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Get a JSON payload, enforcing throttling and the API ``code``."""
        if extra_throttler is None:
            async with self._api_throttler:
                payload, cookies = await self._fetch(url, endpoint)
        else:
            async with self._api_throttler, extra_throttler:
                payload, cookies = await self._fetch(url, endpoint)

        code = _coerce_int(payload.get("code"), 0)
        if code != 0 and code not in allowed_codes:
            raise BiliApiError(code, _message(payload), endpoint)
        return payload, cookies

    # ------------------------------------------------------------------
    # WBI
    # ------------------------------------------------------------------
    async def _get_wbi_keys(self) -> tuple[str, str]:
        """Return cached WBI keys, refreshing them every six hours."""
        now = time.monotonic()
        if self._wbi_img_key and self._wbi_sub_key and now < self._wbi_expires_at:
            return self._wbi_img_key, self._wbi_sub_key

        async with self._wbi_lock:
            # Another coroutine may have refreshed the keys while we waited.
            now = time.monotonic()
            if self._wbi_img_key and self._wbi_sub_key and now < self._wbi_expires_at:
                return self._wbi_img_key, self._wbi_sub_key

            data = await self.nav()
            wbi_img = data.get("wbi_img")
            img_key = sub_key = ""
            if isinstance(wbi_img, dict):
                img_key = _extract_key(str(wbi_img.get("img_url") or ""))
                sub_key = _extract_key(str(wbi_img.get("sub_url") or ""))
            if not img_key or not sub_key:
                raise BiliApiError(-1, "nav response did not contain WBI keys", _NAV_PATH)

            self._wbi_img_key = img_key
            self._wbi_sub_key = sub_key
            self._wbi_expires_at = time.monotonic() + constants.WBI_CACHE_TTL
            return img_key, sub_key

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    async def nav(self) -> dict[str, Any]:
        """Return the nav payload data.

        A logged-out cookie produces ``code=-101`` but still carries a valid
        ``wbi_img`` object, so that code is explicitly accepted.
        """
        payload, _ = await self._request(
            self._build_url(constants.BILI_API, _NAV_PATH),
            _NAV_PATH,
            allowed_codes=frozenset({-101}),
        )
        return _data(payload)

    async def spi(self) -> dict[str, str]:
        """Return the anonymous device fingerprint cookies (``b_3``/``b_4``)."""
        endpoint = "/x/frontend/finger/spi"
        payload, _ = await self._request(self._build_url(constants.BILI_API, endpoint), endpoint)
        data = _data(payload)
        return {
            "b_3": str(data.get("b_3") or ""),
            "b_4": str(data.get("b_4") or ""),
        }

    async def search_video(
        self, keyword: str, page: int = 1, page_size: int = 20
    ) -> list[dict[str, Any]]:
        """Search videos through the WBI-protected search endpoint."""
        img_key, sub_key = await self._get_wbi_keys()
        query = sign_params(
            {
                "search_type": "video",
                "keyword": keyword,
                "page": page,
                "page_size": page_size,
            },
            img_key,
            sub_key,
        )
        url = self._build_url(constants.BILI_API, _SEARCH_PATH, query=query)
        payload, _ = await self._request(
            url,
            _SEARCH_PATH,
            extra_throttler=self._search_throttler,
        )
        return _items(payload, "result")

    async def view(self, bvid: str | None = None, aid: int | None = None) -> dict[str, Any]:
        """Return the video detail ``data`` object for a bvid or an aid."""
        if not bvid and not aid:
            raise ValueError("view() needs either bvid or aid")
        endpoint = "/x/web-interface/view"
        params = {"bvid": bvid} if bvid else {"aid": int(aid or 0)}
        payload, _ = await self._request(
            self._build_url(constants.BILI_API, endpoint, params), endpoint
        )
        return _data(payload)

    async def playurl(
        self,
        *,
        bvid: str | None = None,
        avid: int | None = None,
        cid: int,
    ) -> dict[str, Any]:
        """Return the DASH play info for a video/page."""
        params: dict[str, object] = {}
        if bvid:
            params["bvid"] = bvid
        elif avid is not None:
            params["avid"] = avid
        else:
            raise ValueError("playurl requires either bvid or avid")
        params["cid"] = cid
        params["qn"] = 0
        params["fnver"] = 0
        params["fnval"] = 16
        params["fourk"] = 1

        endpoint = "/x/player/playurl"
        payload, _ = await self._request(
            self._build_url(constants.BILI_API, endpoint, params), endpoint
        )
        return _data(payload)

    async def ranking_region(self, rid: int = 3, day: int = 3) -> list[dict[str, Any]]:
        """Return the ranking list for a region (rid=3 is the music region)."""
        endpoint = "/x/web-interface/ranking/region"
        payload, _ = await self._request(
            self._build_url(constants.BILI_API, endpoint, {"rid": rid, "day": day}),
            endpoint,
        )
        return _items(payload, "list")

    async def music_rank(self, ps: int = 30) -> list[dict[str, Any]]:
        """Return the music centre comprehensive ranking."""
        endpoint = "/x/centralization/interface/music/comprehensive/web/rank"
        payload, _ = await self._request(
            self._build_url(constants.BILI_API, endpoint, {"pn": 1, "ps": ps}), endpoint
        )
        return _items(payload, "list")

    async def new_music(self) -> list[dict[str, Any]]:
        """Return the music centre "new music" list."""
        endpoint = "/x/centralization/interface/new/music"
        payload, _ = await self._request(self._build_url(constants.BILI_API, endpoint), endpoint)
        return _items(payload, "list")

    async def rcmd(self, ps: int = 10) -> list[dict[str, Any]]:
        """Return the home page recommended videos."""
        endpoint = "/x/web-interface/index/top/rcmd"
        payload, _ = await self._request(
            self._build_url(constants.BILI_API, endpoint, {"ps": ps}), endpoint
        )
        return _items(payload, "item", "list")

    async def fav_folders(self, mid: int) -> list[dict[str, Any]]:
        """Return the favourite folders created by *mid*."""
        endpoint = "/x/v3/fav/folder/created/list-all"
        payload, _ = await self._request(
            self._build_url(constants.BILI_API, endpoint, {"up_mid": mid}), endpoint
        )
        return _items(payload, "list")

    async def fav_collected(self, mid: int, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        """Return one page of favourite folders collected from other users."""
        endpoint = "/x/v3/fav/folder/collected/list"
        payload, _ = await self._request(
            self._build_url(
                constants.BILI_API,
                endpoint,
                {"up_mid": mid, "pn": page, "ps": page_size},
            ),
            endpoint,
        )
        return _data(payload)

    async def fav_items(self, media_id: int, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        """Return one page of a favourite folder's contents."""
        endpoint = "/x/v3/fav/resource/list"
        payload, _ = await self._request(
            self._build_url(
                constants.BILI_API,
                endpoint,
                {
                    "media_id": media_id,
                    "pn": page,
                    "ps": page_size,
                    "platform": "web",
                },
            ),
            endpoint,
        )
        return _data(payload)

    async def qrcode_generate(self) -> dict[str, Any]:
        """Create a login QR code and return its ``url``/``qrcode_key``."""
        endpoint = "/x/passport-login/web/qrcode/generate"
        payload, _ = await self._request(
            self._build_url(constants.BILI_PASSPORT, endpoint), endpoint
        )
        return _data(payload)

    async def qrcode_poll(self, qrcode_key: str) -> tuple[int, dict[str, str]]:
        """Poll a login QR code.

        Returns the *inner* ``data.code`` (86101/86090/86038/0) together with
        the cookies set by the successful response. A 200 response with a
        non-zero inner code is a normal state, not an error.
        """
        endpoint = "/x/passport-login/web/qrcode/poll"
        payload, cookies = await self._request(
            self._build_url(constants.BILI_PASSPORT, endpoint, {"qrcode_key": qrcode_key}),
            endpoint,
        )
        data = _data(payload)
        code = _coerce_int(data.get("code"), _coerce_int(payload.get("code"), 0))

        cookie_info = data.get("cookie_info")
        if isinstance(cookie_info, dict):
            cookie_items = cookie_info.get("cookies")
            if isinstance(cookie_items, list):
                for item in cookie_items:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("name")
                    value = item.get("value")
                    if name and value is not None:
                        cookies[str(name)] = str(value)
        return code, cookies
