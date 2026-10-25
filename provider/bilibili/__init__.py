"""Bilibili music provider for Music Assistant.

The provider streams the audio track of Bilibili videos. It is deliberately
read-only: the only account state it touches is the favorite folders and the
login used to obtain higher quality audio streams. All payload parsing lives in
:mod:`bilibili.parsers`, all HTTP in :mod:`bilibili.client` and the favorites /
recommendations / browse tree in :mod:`bilibili.catalog`; this module only wires
them into the Music Assistant provider API.
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator, Iterable, Sequence
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import parse_qs, urlsplit

import aiohttp
from music_assistant.constants import CONF_ENTRY_UNOFFICIAL_PROVIDER, PlaylistPlayableItem
from music_assistant.models.music_provider import MusicProvider
from music_assistant_models.config_entries import ConfigEntry, ConfigValueOption
from music_assistant_models.enums import (
    ConfigEntryType,
    ContentType,
    MediaType,
    ProviderFeature,
    StreamType,
)
from music_assistant_models.errors import (
    MediaNotFoundError,
    MusicAssistantError,
    ProviderUnavailableError,
    ResourceTemporarilyUnavailable,
    UnplayableMediaError,
)
from music_assistant_models.media_items import (
    Artist,
    AudioFormat,
    BrowseFolder,
    ItemMapping,
    MediaItemType,
    Playlist,
    ProviderMapping,
    RecommendationFolder,
    SearchResults,
    Track,
    UniqueList,
)
from music_assistant_models.streamdetails import StreamDetails

from . import catalog, constants, parsers
from .auth import BilibiliCredential
from .client import AudioStream, BiliApiError, BilibiliClient
from .compat import check_compat

if TYPE_CHECKING:
    from music_assistant.mass import MusicAssistant
    from music_assistant.models import ProviderInstanceType
    from music_assistant_models.config_entries import ProviderConfig
    from music_assistant_models.provider import ProviderManifest

SUPPORTED_FEATURES = {
    ProviderFeature.SEARCH,
    ProviderFeature.LIBRARY_PLAYLISTS,
    ProviderFeature.RECOMMENDATIONS,
    ProviderFeature.BROWSE,
}

#: Human readable titles for the quality select; values come from constants.
_QUALITY_TITLES: Final = {
    constants.QUALITY_AUTO: "自动（最高可用）",
    constants.QUALITY_FLAC: "FLAC 无损",
    constants.QUALITY_DOLBY: "Dolby 多声道",
    constants.QUALITY_AAC192: "AAC 192k",
    constants.QUALITY_AAC132: "AAC 132k",
    constants.QUALITY_AAC64: "AAC 64k",
}

#: A playurl URL carries its own expiry timestamp; without one, stay conservative.
_DEFAULT_STREAM_EXPIRATION: Final = 600

#: How many URLs of one stream we are willing to probe (base + two backups).
_MAX_PROBED_URLS: Final = 3

#: Upper bound for the number of view payloads kept in the in-memory cache.
_VIEW_CACHE_MAX: Final = 256

#: Search API page size bounds (the endpoint rejects oversized pages).
_SEARCH_PAGE_SIZE_MIN: Final = 20
_SEARCH_PAGE_SIZE_MAX: Final = 50

_TRACK_MEDIA_TYPE_ERROR: Final = "Bilibili can only stream tracks, not {media_type}"


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Initialize the provider."""
    return BilibiliProvider(mass, manifest, config, SUPPORTED_FEATURES)


# ---------------------------------------------------------------------------
# small pure helpers
# ---------------------------------------------------------------------------
def _as_int(value: Any, default: int = 0) -> int:
    """Best effort int conversion for JSON numbers that may arrive as strings."""
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except TypeError, ValueError:
        try:
            return int(float(value))
        except TypeError, ValueError, OverflowError:
            return default


def _as_bool(value: Any, default: bool = False) -> bool:
    """Interpret the common boolean spellings, falling back to *default*."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _host(url: str) -> str:
    """Return the host of *url* so logs never contain a signed stream URL."""
    try:
        return urlsplit(url).hostname or "unknown"
    except ValueError:
        return "unknown"


def _packed_artist_names(track: Track) -> list[str]:
    """Return the (already normalized) artist names of *track*."""
    return [str(artist.name) for artist in track.artists if getattr(artist, "name", None)]


def _popularity(track: Track) -> int:
    """Return the play counter of *track*, or 0 when unknown."""
    metadata = track.metadata
    value = metadata.popularity if metadata is not None else None
    return _as_int(value, 0)


def _dedupe_key(track: Track) -> tuple[str, str]:
    """Build the duplicate key: cleaned title plus UP-owner name."""
    artist = next((name for name in _packed_artist_names(track)), "")
    return (track.name or "").strip().casefold(), artist.strip().casefold()


def _stream_expiration(url: str) -> int:
    """Clamp the URL deadline to a sane StreamDetails lifetime."""
    try:
        query = parse_qs(urlsplit(url).query)
    except ValueError:
        query = {}
    deadline = 0
    for key in ("deadline", "expire"):
        values = query.get(key)
        if values:
            deadline = _as_int(values[0], 0)
            if deadline:
                break
    if deadline <= 0:
        return _DEFAULT_STREAM_EXPIRATION
    remaining = deadline - int(time.time()) - constants.STREAM_EXPIRATION_MARGIN
    return max(
        constants.STREAM_EXPIRATION_MIN,
        min(constants.STREAM_EXPIRATION_MAX, remaining),
    )


def _audio_rank(stream: AudioStream) -> int:
    """Return the preference rank of an audio stream (unknown ids rank lowest)."""
    return constants.AUDIO_ID_RANK.get(stream.stream_id, 0)


def _normalise_codec(
    stream_id: int, entry: dict[str, Any], fallback_codec: str, mime_type: str
) -> str:
    """Map an audio candidate to ``aac`` / ``flac`` / ``eac3`` / ``unknown``."""
    if stream_id == 30251:
        return "flac"
    if stream_id == 30250:
        return "eac3"
    if stream_id in (30216, 30232, 30280):
        return "aac"

    raw_codec = str(entry.get("codecs") or entry.get("codec") or "").strip().lower()
    haystack = f"{raw_codec} {fallback_codec} {mime_type}".lower()
    if "flac" in haystack:
        return "flac"
    if "ec-3" in haystack or "eac3" in haystack or "ac-3" in haystack:
        return "eac3"
    if "mp4a" in haystack or "aac" in haystack:
        return "aac"
    return raw_codec or "unknown"


def _build_candidate(entry: Any, fallback_id: int, fallback_codec: str) -> AudioStream | None:
    """Normalise one raw ``dash`` audio entry, or return None when unusable."""
    if not isinstance(entry, dict):
        return None
    url = str(entry.get("baseUrl") or entry.get("base_url") or entry.get("url") or "").strip()
    if not url:
        return None

    stream_id = _as_int(entry.get("id"), fallback_id)
    bandwidth = _as_int(entry.get("bandwidth"), 0)
    mime_type = str(entry.get("mimeType") or entry.get("mime_type") or "").strip()
    codec = _normalise_codec(stream_id, entry, fallback_codec, mime_type)

    raw_backups = entry.get("backupUrl") or entry.get("backup_url") or []
    if isinstance(raw_backups, str):
        raw_backups = [raw_backups]
    backups: list[str] = []
    if isinstance(raw_backups, list):
        for backup in raw_backups:
            if isinstance(backup, str) and backup.strip() and backup.strip() != url:
                backups.append(backup.strip())

    return AudioStream(
        stream_id=stream_id,
        bandwidth=bandwidth,
        mime_type=mime_type,
        codec=codec,
        url=url,
        backups=backups,
    )


def _candidate_entries(value: Any, fallback_id: int, fallback_codec: str) -> list[AudioStream]:
    """Normalise a list/dict-shaped ``audio`` field into candidates."""
    if isinstance(value, dict):
        entries: list[Any] = [value]
    elif isinstance(value, list):
        entries = value
    else:
        return []
    result: list[AudioStream] = []
    for entry in entries:
        candidate = _build_candidate(entry, fallback_id, fallback_codec)
        if candidate is not None:
            result.append(candidate)
    return result


def _collect_candidates(dash: dict[str, Any]) -> list[AudioStream]:
    """Collect every audio candidate from a DASH payload.

    ``dash.audio`` is a list, ``dash.dolby.audio`` a list and
    ``dash.flac.audio`` either a dict or a list; membership fields may be
    ``null`` when the account may not use them.
    """
    candidates: list[AudioStream] = []
    raw_audio = dash.get("audio")
    if isinstance(raw_audio, list):
        for entry in raw_audio:
            candidate = _build_candidate(entry, 0, "")
            if candidate is not None:
                candidates.append(candidate)

    dolby = dash.get("dolby")
    if isinstance(dolby, dict):
        candidates.extend(_candidate_entries(dolby.get("audio"), 30250, "eac3"))

    flac = dash.get("flac")
    if isinstance(flac, dict):
        candidates.extend(_candidate_entries(flac.get("audio"), 30251, "flac"))

    return candidates


def _audio_format(stream: AudioStream) -> AudioFormat:
    """Translate a normalized candidate into an MA ``AudioFormat``."""
    bit_rate = stream.bandwidth or None
    if stream.codec == "flac":
        return AudioFormat(
            content_type=ContentType.FLAC,
            codec_type=ContentType.FLAC,
            sample_rate=48000,
            bit_depth=24,
            channels=2,
            bit_rate=bit_rate,
        )
    if stream.codec == "eac3":
        return AudioFormat(
            content_type=ContentType.EAC3,
            codec_type=ContentType.EAC3,
            sample_rate=48000,
            channels=6,
            bit_rate=bit_rate,
        )
    # Bilibili's AAC streams are 48 kHz stereo.
    return AudioFormat(
        content_type=ContentType.M4A,
        codec_type=ContentType.AAC,
        sample_rate=48000,
        channels=2,
        bit_rate=bit_rate,
    )


def _describe_error(err: Exception) -> str:
    """Return a short, URL-free description for a warning log line."""
    detail = str(err).strip()
    return f"{type(err).__name__}: {detail}" if detail else type(err).__name__


class BilibiliProvider(MusicProvider):
    """Bilibili video audio as a Music Assistant music source."""

    @property
    def max_concurrent_streams(self) -> int:
        """Keep a conservative number of parallel Bilibili CDN streams."""
        return 3

    def __init__(
        self,
        mass: MusicAssistant,
        manifest: ProviderManifest,
        config: ProviderConfig,
        supported_features: set[ProviderFeature] | None = None,
    ) -> None:
        """Initialize the provider state (the network client is built in async init)."""
        super().__init__(mass, manifest, config, supported_features)
        self._client: BilibiliClient | None = None
        self._user_name: str = ""
        self._mid: int = 0
        self._artist_names: dict[str, str] = {}
        # bvid -> (monotonic expiry, raw view payload)
        self._view_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        # CDN host -> (healthy, monotonic expiry)
        self._cdn_health: dict[str, tuple[bool, float]] = {}
        # playlist id -> (monotonic expiry, all playable tracks)
        self._fav_tracks_cache: dict[str, tuple[float, list[Track]]] = {}

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def handle_async_init(self) -> None:
        """Validate the runtime, read the stored credential and prepare the client.

        An invalid or expired login must never keep the provider from loading:
        account features simply stay unavailable and a warning is logged.
        """
        if issues := check_compat():
            raise ProviderUnavailableError("; ".join(issues))

        credential = self._load_credential()
        self._mid = credential.mid
        client = BilibiliClient(self.mass, credential, self.logger)
        self._client = client

        if credential.sessdata:
            await self._verify_login(client)

        if not client.credential.has_login:
            await self._ensure_buvid()

        self.logger.debug(
            "Bilibili provider ready (logged in: %s, mid: %s)",
            client.credential.has_login,
            self._mid,
        )

    async def unload(self, is_removed: bool = False) -> None:
        """Release the client reference and drop the in-memory caches."""
        client = self._client
        self._client = None
        self._view_cache.clear()
        self._artist_names.clear()
        self._cdn_health.clear()
        self._fav_tracks_cache.clear()
        if client is not None:
            await client.close()

    def _load_credential(self) -> BilibiliCredential:
        """Read the setup-flow credential, then the manual SESSDATA fallback."""
        raw = self.get_setup_value(constants.CONF_CREDENTIAL_JSON)
        credential = BilibiliCredential.from_json(raw if isinstance(raw, str) else None)
        if credential.sessdata or credential.dedeuserid:
            return credential

        manual = self.get_config_value(constants.CONF_SESSDATA_MANUAL)
        sessdata = str(manual or "").strip()
        if sessdata:
            return BilibiliCredential(sessdata=sessdata, saved_at=int(time.time()))
        return BilibiliCredential()

    async def _verify_login(self, client: BilibiliClient) -> None:
        """Validate a stored login once; invalid credentials degrade to anonymous."""
        try:
            nav = await client.nav()
        except BiliApiError as err:
            if err.code == -101:
                self.logger.warning(
                    "The stored Bilibili credential is not logged in; "
                    "falling back to anonymous access"
                )
                self._degrade_to_anonymous(client)
                return
            # Rate limiting / risk control says nothing about the credential.
            self.logger.warning(
                "Bilibili login could not be validated (%s); keeping the saved credential",
                _describe_error(err),
            )
            return
        except (ResourceTemporarilyUnavailable, aiohttp.ClientError, TimeoutError, OSError) as err:
            # A temporary network problem is not proof that the login is invalid.
            self.logger.warning(
                "Could not reach Bilibili to validate the login (%s); keeping the saved credential",
                _describe_error(err),
            )
            return

        mid = _as_int(nav.get("mid"), 0) or client.credential.mid
        # nav is authoritative: a stored DedeUserID must not mask a logged-out
        # response (an expired SESSDATA still carries the old cookie).
        nav_login = nav.get("isLogin")
        is_login = _as_bool(nav_login) if nav_login is not None else mid > 0
        if not is_login:
            self.logger.warning(
                "The stored Bilibili credential is no longer valid; "
                "falling back to anonymous access"
            )
            self._degrade_to_anonymous(client)
            return

        self._user_name = str(nav.get("uname") or "").strip()
        self._mid = mid
        if mid and client.credential.dedeuserid != str(mid):
            # A manually entered SESSDATA has no DedeUserID: fill it in from nav.
            client.credential = client.credential.merged(
                BilibiliCredential(
                    sessdata=client.credential.sessdata,
                    dedeuserid=str(mid),
                    saved_at=int(time.time()),
                )
            )

    def _degrade_to_anonymous(self, client: BilibiliClient) -> None:
        """Drop the rejected secrets but keep the device fingerprint cookies."""
        old = client.credential
        client.credential = BilibiliCredential(
            buvid3=old.buvid3,
            buvid4=old.buvid4,
            saved_at=int(time.time()),
        )
        self._user_name = ""
        self._mid = 0

    async def _ensure_buvid(self) -> None:
        """Fetch the anonymous device cookies once; failures are not fatal."""
        client = self._client
        if client is None or client.credential.buvid3:
            return
        try:
            fingerprint = await client.spi()
        except (
            BiliApiError,
            ResourceTemporarilyUnavailable,
            aiohttp.ClientError,
            TimeoutError,
            OSError,
        ) as err:
            self.logger.warning(
                "Could not fetch Bilibili device cookies (%s); continuing without them",
                _describe_error(err),
            )
            return

        buvid3 = str(fingerprint.get("b_3") or "").strip()
        buvid4 = str(fingerprint.get("b_4") or "").strip()
        if buvid3 or buvid4:
            client.credential = client.credential.with_buvid(buvid3, buvid4)

    # ------------------------------------------------------------------
    # configuration
    # ------------------------------------------------------------------
    async def get_config_entries(self) -> tuple[ConfigEntry, ...]:
        """Return the provider options; credentials are collected by the setup flow."""
        return (
            ConfigEntry(
                key=constants.CONF_QUALITY,
                type=ConfigEntryType.STRING,
                default_value=constants.QUALITY_AUTO,
                required=True,
                options=[
                    ConfigValueOption(quality, title=_QUALITY_TITLES.get(quality, quality))
                    for quality in constants.QUALITY_OPTIONS
                ],
            ),
            ConfigEntry(
                key=constants.CONF_MIN_DURATION,
                type=ConfigEntryType.INTEGER,
                default_value=constants.DEFAULT_MIN_DURATION,
                required=True,
                range=(0, 36000),
            ),
            ConfigEntry(
                key=constants.CONF_MAX_DURATION,
                type=ConfigEntryType.INTEGER,
                default_value=constants.DEFAULT_MAX_DURATION,
                required=True,
                range=(0, 36000),
            ),
            ConfigEntry(
                key=constants.CONF_SEARCH_LIMIT,
                type=ConfigEntryType.INTEGER,
                default_value=constants.DEFAULT_SEARCH_LIMIT,
                required=True,
                range=(1, _SEARCH_PAGE_SIZE_MAX),
            ),
            ConfigEntry(
                key=constants.CONF_VERIFY_STREAM_URL,
                type=ConfigEntryType.BOOLEAN,
                default_value=True,
                required=True,
            ),
            ConfigEntry(
                key=constants.CONF_SESSDATA_MANUAL,
                type=ConfigEntryType.SECURE_STRING,
                default_value="",
                required=False,
            ),
            CONF_ENTRY_UNOFFICIAL_PROVIDER,
        )

    def _config_int(self, key: str, default: int) -> int:
        """Read an integer option, falling back to *default* for malformed values."""
        return _as_int(self.get_config_value(key, default), default)

    def _config_bool(self, key: str, default: bool) -> bool:
        """Read a boolean option, falling back to *default* for malformed values."""
        return _as_bool(self.get_config_value(key, default), default)

    def _config_str(self, key: str, default: str) -> str:
        """Read a string option, falling back to *default* when empty."""
        value = str(self.get_config_value(key, default) or "").strip()
        return value or default

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _require_client(self) -> BilibiliClient:
        """Return the initialized client, or raise when the provider is not ready."""
        if self._client is None:
            raise ProviderUnavailableError("Bilibili provider is not initialized")
        return self._client

    @staticmethod
    def _split_track_id(item_id: Any) -> tuple[str, int | None]:
        """Split ``bvid`` / ``bvid#cid`` into a video id and an optional cid."""
        raw = str(item_id or "").strip()
        if not raw:
            raise MediaNotFoundError("Bilibili track id is empty")
        base, separator, suffix = raw.partition("#")
        base = base.strip()
        if not base:
            raise MediaNotFoundError(f"Invalid Bilibili track id: {raw!r}")
        cid: int | None = None
        if separator and suffix.strip():
            cid = _as_int(suffix.strip(), 0) or None
        return base, cid

    @staticmethod
    def _avid(base: str) -> int | None:
        """Return the aid when *base* is an ``av<number>`` id."""
        lowered = base.lower()
        if lowered.startswith("av") and lowered[2:].isdigit():
            return int(lowered[2:])
        return None

    @staticmethod
    def _page_for_cid(data: dict[str, Any], cid: int) -> dict[str, Any] | None:
        """Return the ``pages`` entry with the requested cid, if any."""
        pages = data.get("pages")
        if not isinstance(pages, list):
            return None
        for page in pages:
            if isinstance(page, dict) and _as_int(page.get("cid"), 0) == cid:
                return page
        return None

    async def _view(self, video_id: str) -> dict[str, Any]:
        """Return a view payload, cached in memory for 24 hours."""
        now = time.monotonic()
        cached = self._view_cache.get(video_id)
        if cached is not None and cached[0] > now:
            return cached[1]

        avid = self._avid(video_id)
        try:
            data = (
                await self._require_client().view(aid=avid)
                if avid is not None
                else await self._require_client().view(video_id)
            )
        except BiliApiError as err:
            raise self._translate_error(err) from err

        if len(self._view_cache) >= _VIEW_CACHE_MAX:
            live = {key: value for key, value in self._view_cache.items() if value[0] > now}
            if len(live) >= _VIEW_CACHE_MAX:
                # Drop the entry that expires first instead of growing past the cap.
                live.pop(min(live, key=lambda key: live[key][0]), None)
            self._view_cache = live
        self._view_cache[video_id] = (now + constants.VIEW_CACHE_TTL, data)
        return data

    async def _first_cid(self, video_id: str) -> int:
        """Resolve the first playable cid of a video."""
        data = await self._view(video_id)
        pages = data.get("pages")
        if isinstance(pages, list):
            for page in pages:
                if isinstance(page, dict):
                    cid = _as_int(page.get("cid"), 0)
                    if cid:
                        return cid
        cid = _as_int(data.get("cid"), 0)
        if not cid:
            raise UnplayableMediaError(f"Bilibili video {video_id} has no playable page")
        return cid

    def _remember_artist_names(self, items: Iterable[Any]) -> None:
        """Remember UP-owner ids/names so get_artist can answer from memory."""
        for item in items:
            for artist in getattr(item, "artists", None) or ():
                artist_id = str(getattr(artist, "item_id", "") or "").strip()
                name = str(getattr(artist, "name", "") or "").strip()
                if artist_id and name:
                    self._artist_names.setdefault(artist_id, name)

    def _duration_allowed(self, duration: int) -> bool:
        """Apply the configured min/max track duration filter.

        Unknown durations are kept: dropping them would hide valid results while
        the API simply did not report a length.
        """
        if duration <= 0:
            return True
        minimum = self._config_int(constants.CONF_MIN_DURATION, constants.DEFAULT_MIN_DURATION)
        maximum = self._config_int(constants.CONF_MAX_DURATION, constants.DEFAULT_MAX_DURATION)
        if minimum > 0 and duration < minimum:
            return False
        return not (maximum > 0 and duration > maximum)

    def _dedupe_tracks(self, tracks: list[Track]) -> list[Track]:
        """Drop duplicates (clean title + author), keeping the most played entry."""
        kept: dict[tuple[str, str], Track] = {}
        for track in tracks:
            key = _dedupe_key(track)
            existing = kept.get(key)
            if existing is None or _popularity(track) > _popularity(existing):
                kept[key] = track
        return list(kept.values())

    def _handle_auth_error(self, client: BilibiliClient, err: BiliApiError) -> bool:
        """Degrade to anonymous once when Bilibili reports an expired login."""
        if err.code != -101:
            return False
        self.logger.warning(
            "The Bilibili credential expired; favorite folders are unavailable until re-login"
        )
        self._degrade_to_anonymous(client)
        return True

    @staticmethod
    def _translate_error(err: BiliApiError) -> MusicAssistantError:
        """Translate a Bilibili API error into the matching Music Assistant error."""
        code = err.code
        endpoint = err.endpoint or "bilibili"
        if code == -352:
            return ResourceTemporarilyUnavailable(
                f"Bilibili rate limited the request at {endpoint}",
                backoff_time=30,
            )
        if code == -412:
            return ResourceTemporarilyUnavailable(
                f"Bilibili rejected the request at {endpoint}",
                backoff_time=60,
            )
        if code in (-404, 62002, 62004):
            return MediaNotFoundError(f"Bilibili media is unavailable at {endpoint} (code {code})")
        if code in (87007, 87008):
            return UnplayableMediaError(
                f"Bilibili media requires a paid membership at {endpoint} (code {code})"
            )
        return ResourceTemporarilyUnavailable(
            f"Bilibili API error {code} at {endpoint}",
            backoff_time=5,
        )

    # ------------------------------------------------------------------
    # search / lookup
    # ------------------------------------------------------------------
    async def search(
        self,
        search_query: str,
        media_types: list[MediaType],
        limit: int = 5,
    ) -> SearchResults:
        """Search Bilibili videos for the track media type."""
        if MediaType.TRACK not in media_types:
            return SearchResults()

        query = str(search_query or "").strip()
        if not query:
            return SearchResults()

        search_cap = self._config_int(constants.CONF_SEARCH_LIMIT, constants.DEFAULT_SEARCH_LIMIT)
        if search_cap <= 0:
            search_cap = constants.DEFAULT_SEARCH_LIMIT
        requested = _as_int(limit, search_cap)
        if requested <= 0:
            requested = search_cap
        wanted = min(requested, search_cap)
        page_size = min(max(wanted, _SEARCH_PAGE_SIZE_MIN), _SEARCH_PAGE_SIZE_MAX)

        try:
            items = await self._require_client().search_video(query, page=1, page_size=page_size)
        except BiliApiError as err:
            raise self._translate_error(err) from err

        tracks: list[Track] = []
        for item in items or ():
            track = parsers.parse_search_item(item, self.instance_id)
            if track is None:
                continue
            if not self._duration_allowed(track.duration):
                continue
            tracks.append(track)

        tracks = self._dedupe_tracks(tracks)
        tracks.sort(key=_popularity, reverse=True)
        tracks = tracks[:wanted]
        self._remember_artist_names(tracks)
        return SearchResults(tracks=tracks)

    async def get_track(self, prov_track_id: str) -> Track:
        """Return full details for a ``bvid`` / ``bvid#cid`` track id."""
        raw = str(prov_track_id or "").strip()
        base, cid = self._split_track_id(raw)
        data = await self._view(base)

        try:
            track = parsers.parse_view(data, self.instance_id)
        except ValueError as err:
            raise MediaNotFoundError(f"Bilibili video {base} has no usable metadata") from err

        if cid is not None:
            page = self._page_for_cid(data, cid)
            if page is not None:
                part = str(page.get("part") or "").strip()
                if part:
                    # For a multi-part video the page title is the real track name.
                    track.name = parsers.clean_title(part) or part
                page_duration = _as_int(page.get("duration"), 0)
                if page_duration > 0:
                    track.duration = page_duration

        # Always keep the id the caller asked for (the view payload may point at
        # the first page of a multi-part video instead).
        track.item_id = raw
        for mapping in track.provider_mappings:
            mapping.item_id = raw

        self._remember_artist_names([track])
        return track

    async def get_artist(self, prov_artist_id: str) -> Artist:
        """Return an UP owner from the names seen in search/track payloads."""
        artist_id = str(prov_artist_id or "").strip()
        if not artist_id:
            raise MediaNotFoundError("Bilibili artist id is empty")

        name = self._artist_names.get(artist_id) or f"Bilibili UP 主 {artist_id}"
        url = f"https://space.bilibili.com/{artist_id}" if artist_id.isdigit() else None
        return Artist(
            item_id=artist_id,
            provider=self.instance_id,
            name=name,
            provider_mappings={
                ProviderMapping(
                    item_id=artist_id,
                    provider_domain=constants.PROVIDER_DOMAIN,
                    provider_instance=self.instance_id,
                    available=True,
                    url=url,
                )
            },
        )

    # ------------------------------------------------------------------
    # playback
    # ------------------------------------------------------------------
    async def get_stream_details(self, item_id: str, media_type: MediaType) -> StreamDetails:
        """Resolve the best playable audio stream for a Bilibili track."""
        if media_type != MediaType.TRACK:
            raise MediaNotFoundError(_TRACK_MEDIA_TYPE_ERROR.format(media_type=media_type))

        raw = str(item_id or "").strip()
        base, cid = self._split_track_id(raw)
        avid = self._avid(base)

        if cid is None:
            cid = await self._first_cid(base)

        params: dict[str, Any] = {"avid": avid} if avid is not None else {"bvid": base}
        try:
            data = await self._require_client().playurl(**params, cid=cid)
        except BiliApiError as err:
            raise self._translate_error(err) from err

        dash = data.get("dash") if isinstance(data, dict) else None
        if not isinstance(dash, dict):
            raise UnplayableMediaError(f"Bilibili did not return a DASH audio stream for {raw}")

        candidates = _collect_candidates(dash)
        if not candidates:
            raise UnplayableMediaError(f"Bilibili returned no audio candidates for {raw}")

        quality = self._config_str(constants.CONF_QUALITY, constants.QUALITY_AUTO)
        max_rank = constants.QUALITY_MAX_RANK.get(
            quality, constants.QUALITY_MAX_RANK[constants.QUALITY_AUTO]
        )
        eligible = [stream for stream in candidates if _audio_rank(stream) <= max_rank]
        if not eligible:
            # The user set a hard ceiling; do not silently play above it.
            raise UnplayableMediaError(
                f"Bilibili has no audio stream at or below quality {quality} for {raw}"
            )
        chosen = max(eligible, key=lambda stream: (_audio_rank(stream), stream.bandwidth))

        url = chosen.url
        if self._config_bool(constants.CONF_VERIFY_STREAM_URL, True):
            url = await self._select_playable_url(chosen)

        duration = _as_int(dash.get("duration"), 0) or None
        return StreamDetails(
            provider=self.instance_id,
            item_id=raw,
            media_type=MediaType.TRACK,
            audio_format=_audio_format(chosen),
            stream_type=StreamType.HTTP,
            path=url,
            duration=duration,
            can_seek=True,
            allow_seek=True,
            expiration=_stream_expiration(url),
            extra_input_args=list(constants.FFMPEG_HEADERS),
            data={
                "host": _host(url),
                "backup_hosts": [_host(backup) for backup in chosen.backups],
            },
        )

    async def _select_playable_url(self, stream: AudioStream) -> str:
        """Probe the primary URL and its backups, falling back to the primary."""
        for url in (stream.url, *stream.backups)[:_MAX_PROBED_URLS]:
            if await self._probe_url(url):
                return url
        self.logger.warning(
            "All Bilibili CDN probes failed for %s; using the primary URL anyway",
            _host(stream.url),
        )
        return stream.url

    async def _probe_url(self, url: str) -> bool:
        """Check a CDN URL with a small Range request (cached per host)."""
        host = _host(url)
        now = time.monotonic()
        cached = self._cdn_health.get(host)
        if cached is not None and cached[1] > now:
            return cached[0]

        healthy = await self._request_probe(url)
        # A failed host may recover; do not blacklist it for the full TTL.
        ttl = constants.CDN_HEALTH_TTL if healthy else 60
        self._cdn_health[host] = (healthy, now + ttl)
        return healthy

    async def _request_probe(self, url: str) -> bool:
        """Perform the actual 1 KB Range probe."""
        headers = {
            "Referer": constants.BILI_REFERER,
            "Origin": constants.BILI_REFERER,
            "User-Agent": constants.USER_AGENT,
            "Range": f"bytes=0-{constants.PROBE_BYTES - 1}",
        }
        try:
            async with self.mass.http_session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=constants.PROBE_TIMEOUT),
            ) as response:
                status = _as_int(getattr(response, "status", 0), 0)
                return status in (200, 206)
        except TimeoutError, aiohttp.ClientError, OSError, ValueError:
            return False

    # ------------------------------------------------------------------
    # library playlists (Bilibili favorite folders)
    # ------------------------------------------------------------------
    async def get_library_playlists(self) -> AsyncGenerator[Playlist]:
        """Yield the user's favorite folders; anonymous access yields nothing."""
        client = self._client
        if client is None or not client.credential.has_login:
            return
        try:
            async for playlist in catalog.iter_favorite_playlists(client, self.instance_id):
                yield playlist
        except BiliApiError as err:
            if self._handle_auth_error(client, err):
                return
            raise self._translate_error(err) from err

    async def get_playlist(self, prov_playlist_id: str) -> Playlist:
        """Return one favorite folder by its ``fav:<media_id>`` id."""
        client = self._require_client()
        try:
            return await catalog.get_favorite_playlist(client, self.instance_id, prov_playlist_id)
        except BiliApiError as err:
            if self._handle_auth_error(client, err):
                raise MediaNotFoundError(
                    f"Bilibili favorite playlist is unavailable: {prov_playlist_id}"
                ) from err
            raise self._translate_error(err) from err

    async def get_playlist_tracks(
        self, prov_playlist_id: str, page: int = 0
    ) -> Sequence[PlaylistPlayableItem]:
        """Return one page of a favorite folder's tracks."""
        client = self._require_client()
        now = time.monotonic()
        cached = self._fav_tracks_cache.get(prov_playlist_id)
        if cached is not None and cached[0] > now:
            all_tracks = cached[1]
        else:
            try:
                all_tracks = [
                    track
                    async for track in catalog.iter_favorite_tracks(
                        client, self.instance_id, prov_playlist_id
                    )
                ]
            except BiliApiError as err:
                if self._handle_auth_error(client, err):
                    return []
                raise self._translate_error(err) from err
            self._fav_tracks_cache[prov_playlist_id] = (
                now + constants.FAVORITES_CACHE_TTL,
                all_tracks,
            )

        start = max(int(page), 0) * catalog.FAV_PAGE_SIZE
        tracks = all_tracks[start : start + catalog.FAV_PAGE_SIZE]
        self._remember_artist_names(tracks)
        return tracks

    # ------------------------------------------------------------------
    # recommendations / browse
    # ------------------------------------------------------------------
    async def get_recommendations(self) -> list[RecommendationFolder]:
        """Return the static home-screen recommendation rows."""
        return catalog.recommendation_rows(self.instance_id)

    async def get_recommendation_items(
        self, item_id: str
    ) -> UniqueList[MediaItemType | ItemMapping | BrowseFolder]:
        """Return the tracks of one recommendation row."""
        try:
            items = await catalog.recommendation_items(
                self._require_client(), self.instance_id, item_id
            )
        except BiliApiError as err:
            raise self._translate_error(err) from err
        self._remember_artist_names(items)
        return items

    async def browse(self, path: str) -> Sequence[MediaItemType | ItemMapping | BrowseFolder]:
        """Return the Bilibili browse tree."""
        try:
            items = await catalog.browse_items(self._require_client(), self.instance_id, path)
        except BiliApiError as err:
            raise self._translate_error(err) from err
        self._remember_artist_names(items)
        return items
