"""Tests for :mod:`bilibili.client` using a fake aiohttp session (no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import aiohttp
import pytest
from bilibili import constants
from bilibili.auth import BilibiliCredential
from bilibili.client import AudioStream, BiliApiError, BilibiliClient
from bilibili.wbi import sign_params
from music_assistant_models.errors import ResourceTemporarilyUnavailable

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
IMG_KEY = "7cd084941338484aae1ad9425b84077c"
SUB_KEY = "4932caff0ff746eab6f01bf08b70ac45"


def load_fixture(name: str) -> dict[str, Any]:
    """Load a recorded API response."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def nav_payload(code: int = 0) -> dict[str, Any]:
    """Build a nav response; code=-101 still carries valid WBI keys."""
    return {
        "code": code,
        "message": "账号未登录" if code == -101 else "",
        "data": {
            "isLogin": code == 0,
            "wbi_img": {
                "img_url": f"https://i0.hdslb.com/bfs/wbi/{IMG_KEY}.png",
                "sub_url": f"https://i0.hdslb.com/bfs/wbi/{SUB_KEY}.png",
            },
        },
    }


class FakeResponse:
    """Minimal async context manager mimicking an aiohttp response."""

    def __init__(
        self,
        payload: Any,
        *,
        status: int = 200,
        cookies: dict[str, str] | None = None,
    ) -> None:
        self.payload = payload
        self.status = status
        self.cookies = cookies or {}
        self.headers: dict[str, str] = {}

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def json(self, *args: object, **kwargs: object) -> Any:
        return self.payload


class FakeSession:
    """Queue-backed stand-in for ``mass.http_session``."""

    def __init__(self, responses: list[FakeResponse | Exception] | None = None) -> None:
        self.responses = list(responses or [])
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def queue(self, response: FakeResponse | Exception) -> None:
        self.responses.append(response)

    def get(self, url: object, **kwargs: Any) -> FakeResponse:
        self.requests.append((str(url), kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected request: {url}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def urls(self) -> list[str]:
        return [url for url, _ in self.requests]


class FakeMass:
    """Stand-in for the MusicAssistant instance (only ``http_session`` is used)."""

    def __init__(self, responses: list[FakeResponse | Exception] | None = None) -> None:
        self.http_session = FakeSession(responses)


def make_client(
    responses: list[FakeResponse | Exception],
    credential: BilibiliCredential | None = None,
) -> tuple[BilibiliClient, FakeMass]:
    mass = FakeMass(responses)
    return BilibiliClient(mass, credential or BilibiliCredential()), mass


async def test_nav_accepts_logged_out_code_and_sends_headers() -> None:
    """code=-101 is not an error and every request carries browser headers."""
    client, mass = make_client([FakeResponse(nav_payload(-101))])

    data = await client.nav()

    assert data["wbi_img"]["img_url"].endswith(f"{IMG_KEY}.png")
    url, kwargs = mass.http_session.requests[0]
    assert url == f"{constants.BILI_API}/x/web-interface/nav"
    headers = kwargs["headers"]
    assert headers["Referer"] == constants.BILI_REFERER
    assert headers["Origin"] == constants.BILI_REFERER
    assert headers["User-Agent"] == constants.USER_AGENT
    assert "Cookie" not in headers


async def test_credential_adds_cookie_header() -> None:
    """A logged in credential is sent as a Cookie header."""
    credential = BilibiliCredential(
        sessdata="sess", bili_jct="jct", dedeuserid="42", buvid3="b3", buvid4="b4"
    )
    client, mass = make_client([FakeResponse(nav_payload())], credential)

    await client.nav()

    cookie = mass.http_session.requests[0][1]["headers"]["Cookie"]
    assert cookie == "SESSDATA=sess; bili_jct=jct; DedeUserID=42; buvid3=b3; buvid4=b4"


async def test_search_video_sends_signed_query_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The signed query is not encoded a second time by the HTTP layer."""
    monkeypatch.setattr("bilibili.wbi.time.time", lambda: 1700000000.0)
    client, mass = make_client(
        [FakeResponse(nav_payload()), FakeResponse(load_fixture("search_video.json"))]
    )

    results = await client.search_video("周杰伦 晴天")

    expected_query = sign_params(
        {
            "search_type": "video",
            "keyword": "周杰伦 晴天",
            "page": 1,
            "page_size": 20,
        },
        IMG_KEY,
        SUB_KEY,
        wts=1700000000,
    )
    assert results
    assert results[0]["bvid"] == "BV1BZbSzZEGT"
    assert mass.http_session.urls() == [
        f"{constants.BILI_API}/x/web-interface/nav",
        f"{constants.BILI_API}/x/web-interface/wbi/search/type?{expected_query}",
    ]
    search_url = mass.http_session.urls()[1]
    assert "%25" not in search_url


async def test_wbi_keys_are_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only one nav request is made for repeated searches within six hours."""
    monkeypatch.setattr("bilibili.wbi.time.time", lambda: 1700000000.0)
    search_fixture = load_fixture("search_video.json")
    client, mass = make_client(
        [
            FakeResponse(nav_payload()),
            FakeResponse(search_fixture),
            FakeResponse(search_fixture),
        ]
    )

    await client.search_video("晴天")
    await client.search_video("晴天")

    nav_requests = [url for url in mass.http_session.urls() if "/x/web-interface/nav" in url]
    assert len(nav_requests) == 1
    assert len(mass.http_session.urls()) == 3


async def test_api_error_carries_code_message_and_endpoint() -> None:
    """A non-zero API code becomes BiliApiError with the endpoint path."""
    client, _ = make_client([FakeResponse({"code": -352, "message": "风控校验失败", "data": None})])

    with pytest.raises(BiliApiError) as exc_info:
        await client.view("BV1GJ411x7h7")

    error = exc_info.value
    assert error.code == -352
    assert error.message == "风控校验失败"
    assert error.endpoint == "/x/web-interface/view"
    assert "api.bilibili.com" not in str(error)


async def test_view_and_playurl() -> None:
    """view returns data and playurl sends the DASH parameters."""
    client, mass = make_client(
        [FakeResponse(load_fixture("view.json")), FakeResponse(load_fixture("playurl.json"))]
    )

    view = await client.view("BV1GJ411x7h7")
    play = await client.playurl(bvid="BV1GJ411x7h7", cid=137649199)

    assert view["bvid"] == "BV1GJ411x7h7"
    assert play["dash"]["audio"]
    view_query = parse_qs(urlsplit(mass.http_session.urls()[0]).query)
    assert view_query == {"bvid": ["BV1GJ411x7h7"]}
    play_query = parse_qs(urlsplit(mass.http_session.urls()[1]).query)
    assert play_query == {
        "bvid": ["BV1GJ411x7h7"],
        "cid": ["137649199"],
        "qn": ["0"],
        "fnver": ["0"],
        "fnval": ["16"],
        "fourk": ["1"],
    }


async def test_playurl_accepts_avid() -> None:
    """Music centre fallbacks use avid + cid instead of bvid."""
    client, mass = make_client([FakeResponse(load_fixture("playurl.json"))])

    await client.playurl(avid=80433022, cid=137649199)

    query = parse_qs(urlsplit(mass.http_session.urls()[0]).query)
    assert query["avid"] == ["80433022"]
    assert "bvid" not in query


async def test_playurl_requires_an_id() -> None:
    """At least bvid or avid must be supplied."""
    client, mass = make_client([])

    with pytest.raises(ValueError, match="bvid or avid"):
        await client.playurl(cid=1)
    assert mass.http_session.requests == []


async def test_qrcode_generate_and_poll() -> None:
    """Poll returns the inner data.code and cookies from the response."""
    client, mass = make_client(
        [
            FakeResponse(
                {
                    "code": 0,
                    "data": {"url": "https://passport.bilibili.com/h5/...", "qrcode_key": "key1"},
                }
            ),
            FakeResponse(
                {"code": 0, "data": {"code": 86101, "message": "未扫码"}},
                cookies={"SESSDATA": "sess", "bili_jct": "jct", "DedeUserID": "42"},
            ),
        ]
    )

    generated = await client.qrcode_generate()
    code, cookies = await client.qrcode_poll("key1")

    assert generated["qrcode_key"] == "key1"
    assert code == 86101
    assert cookies == {"SESSDATA": "sess", "bili_jct": "jct", "DedeUserID": "42"}
    generate_url, poll_url = mass.http_session.urls()
    assert generate_url == (f"{constants.BILI_PASSPORT}/x/passport-login/web/qrcode/generate")
    assert poll_url.startswith(f"{constants.BILI_PASSPORT}/x/passport-login/web/qrcode/poll?")
    assert parse_qs(urlsplit(poll_url).query) == {"qrcode_key": ["key1"]}


async def test_qrcode_poll_falls_back_to_cookie_info() -> None:
    """Some responses only expose cookies inside data.cookie_info."""
    client, _ = make_client(
        [
            FakeResponse(
                {
                    "code": 0,
                    "data": {
                        "code": 0,
                        "cookie_info": {
                            "cookies": [
                                {"name": "SESSDATA", "value": "sess"},
                                {"name": "DedeUserID", "value": "42"},
                            ]
                        },
                    },
                }
            )
        ]
    )

    code, cookies = await client.qrcode_poll("key1")

    assert code == 0
    assert cookies == {"SESSDATA": "sess", "DedeUserID": "42"}


async def test_network_error_becomes_temporarily_unavailable() -> None:
    """Connection failures are retryable MA errors, not API errors."""
    client, _ = make_client([aiohttp.ClientConnectionError("boom")])

    with pytest.raises(ResourceTemporarilyUnavailable) as exc_info:
        await client.nav()

    assert exc_info.value.backoff_time == 5


async def test_http_500_becomes_temporarily_unavailable() -> None:
    """5xx responses are retryable."""
    client, _ = make_client([FakeResponse(None, status=502)])

    with pytest.raises(ResourceTemporarilyUnavailable) as exc_info:
        await client.spi()

    assert exc_info.value.backoff_time == 30


async def test_spi_returns_device_cookies() -> None:
    """spi normalises the b_3/b_4 payload."""
    client, mass = make_client(
        [FakeResponse({"code": 0, "data": {"b_3": "buvid3-value", "b_4": "buvid4-value"}})]
    )

    result = await client.spi()

    assert result == {"b_3": "buvid3-value", "b_4": "buvid4-value"}
    assert mass.http_session.urls() == [f"{constants.BILI_API}/x/frontend/finger/spi"]


async def test_ranking_region_and_music_sources() -> None:
    """Ranking and music centre endpoints return their list payloads."""
    client, mass = make_client(
        [
            FakeResponse(load_fixture("ranking_region.json")),
            FakeResponse(load_fixture("music_rank.json")),
            FakeResponse(load_fixture("new_music.json")),
            FakeResponse({"code": 0, "data": {"item": [{"bvid": "BV1"}]}}),
        ]
    )

    region = await client.ranking_region()
    music = await client.music_rank()
    new = await client.new_music()
    recommended = await client.rcmd()

    assert region and region[0]["bvid"] == "BV17VZpY1ENA"
    assert music and "music_title" in music[0]
    assert new and "music_title" in new[0]
    assert recommended == [{"bvid": "BV1"}]
    urls = mass.http_session.urls()
    assert parse_qs(urlsplit(urls[0]).query) == {"rid": ["3"], "day": ["3"]}
    assert parse_qs(urlsplit(urls[1]).query) == {"pn": ["1"], "ps": ["30"]}
    assert parse_qs(urlsplit(urls[3]).query) == {"ps": ["10"]}


async def test_favorite_endpoints() -> None:
    """Favourite folder/item calls send the documented pagination parameters."""
    client, mass = make_client(
        [
            FakeResponse(load_fixture("fav_folders.json")),
            FakeResponse({"code": 0, "data": {"list": [], "has_more": False}}),
            FakeResponse(load_fixture("fav_items.json")),
        ]
    )

    folders = await client.fav_folders(42)
    collected = await client.fav_collected(42, page=2, page_size=10)
    items = await client.fav_items(123456, page=3, page_size=20)

    assert folders and folders[0]["id"] == 123456
    assert collected["has_more"] is False
    assert items["medias"]
    urls = mass.http_session.urls()
    assert parse_qs(urlsplit(urls[0]).query) == {"up_mid": ["42"]}
    assert parse_qs(urlsplit(urls[1]).query) == {
        "up_mid": ["42"],
        "pn": ["2"],
        "ps": ["10"],
    }
    assert parse_qs(urlsplit(urls[2]).query) == {
        "media_id": ["123456"],
        "pn": ["3"],
        "ps": ["20"],
        "platform": ["web"],
    }


async def test_close_is_a_noop() -> None:
    """The shared MA session must not be closed by the provider."""
    client, _ = make_client([])

    assert await client.close() is None


def test_audio_stream_defaults() -> None:
    """AudioStream keeps the raw player fields and defaults backups to []."""
    stream = AudioStream(
        stream_id=30280,
        bandwidth=203786,
        mime_type="audio/mp4",
        codec="aac",
        url="https://cdn.example.invalid/a.m4s",
    )
    assert stream.backups == []
    assert stream.stream_id == 30280
