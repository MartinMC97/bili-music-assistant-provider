"""Unit tests for the Bilibili provider class (fully offline, fake client/session)."""

from __future__ import annotations

import copy
import time
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from bilibili import BilibiliProvider, constants, parsers, setup
from bilibili.auth import BilibiliCredential
from bilibili.client import BiliApiError
from music_assistant.constants import CONF_ENTRY_UNOFFICIAL_PROVIDER
from music_assistant_models.config_entries import ProviderConfig
from music_assistant_models.enums import (
    ConfigEntryType,
    ContentType,
    MediaType,
    ProviderFeature,
    ProviderStage,
    ProviderType,
    StreamType,
)
from music_assistant_models.errors import (
    MediaNotFoundError,
    ResourceTemporarilyUnavailable,
    UnplayableMediaError,
)

PROVIDER = "bilibili_test"
MANIFEST = SimpleNamespace(
    type=ProviderType.MUSIC,
    domain="bilibili",
    name="Bilibili",
    stage=ProviderStage.ALPHA,
)


# ---------------------------------------------------------------------------
# fake Music Assistant objects
# ---------------------------------------------------------------------------
class FakeMassConfig:
    """The slice of ``mass.config`` the provider touches."""

    def __init__(
        self,
        setup_data: dict[str, Any] | None = None,
        raw_values: dict[str, Any] | None = None,
    ) -> None:
        self.setup_data = dict(setup_data or {})
        self.raw_values = dict(raw_values or {})

    def get(self, key: str, default: Any = None) -> Any:
        """Return setup data for the provider path, else the default."""
        text = str(key)
        if text.endswith("/setup_data"):
            return self.setup_data
        if text == "providers":
            return {}
        return default

    def decrypt_string(self, value: str) -> str:
        """Pretend stored secrets are plain text."""
        return value

    def get_raw_provider_config_value(self, instance_id: str, key: str) -> Any:
        """Return an explicitly configured raw override, if any."""
        return self.raw_values.get(key)


class FakeMass:
    """Minimal ``MusicAssistant`` stand-in: cache, config and shared HTTP session."""

    def __init__(
        self,
        http_session: Any = None,
        setup_data: dict[str, Any] | None = None,
        raw_values: dict[str, Any] | None = None,
    ) -> None:
        self.cache = None
        self.http_session = http_session or RecordingHTTPSession()
        self.config = FakeMassConfig(setup_data, raw_values)


class ProbeResponse:
    """Async context manager shaped like an aiohttp response."""

    def __init__(self, status: int) -> None:
        self.status = status

    async def __aenter__(self) -> ProbeResponse:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class RecordingHTTPSession:
    """Record CDN probe requests and return canned statuses."""

    def __init__(self, statuses: list[int] | None = None) -> None:
        self.statuses = list(statuses or [])
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> ProbeResponse:
        self.requests.append((str(url), kwargs))
        status = self.statuses.pop(0) if self.statuses else 200
        return ProbeResponse(status)


class FakeClient:
    """Stand-in for :class:`bilibili.client.BilibiliClient` with canned payloads."""

    def __init__(
        self,
        credential: BilibiliCredential | None = None,
        *,
        search_items: list[dict[str, Any]] | None = None,
        view_data: dict[str, Any] | None = None,
        playurl_data: dict[str, Any] | None = None,
    ) -> None:
        self.credential = credential or BilibiliCredential()
        self.search_items = list(search_items or [])
        self.view_data = view_data or {}
        self.playurl_data = playurl_data or {}
        self.search_error: Exception | None = None
        self.view_error: Exception | None = None
        self.playurl_error: Exception | None = None
        self.calls: list[tuple[Any, ...]] = []
        self.view_calls = 0

    async def search_video(
        self, keyword: str, page: int = 1, page_size: int = 20
    ) -> list[dict[str, Any]]:
        """Return canned search entries."""
        self.calls.append(("search", keyword, page, page_size))
        if self.search_error is not None:
            raise self.search_error
        return list(self.search_items)

    async def view(self, bvid: str) -> dict[str, Any]:
        """Return a canned view payload."""
        self.calls.append(("view", bvid))
        self.view_calls += 1
        if self.view_error is not None:
            raise self.view_error
        return self.view_data

    async def playurl(
        self, *, bvid: str | None = None, avid: int | None = None, cid: int
    ) -> dict[str, Any]:
        """Return a canned playurl payload."""
        self.calls.append(("playurl", bvid, avid, cid))
        if self.playurl_error is not None:
            raise self.playurl_error
        return self.playurl_data

    async def fav_folders(self, mid: int) -> list[dict[str, Any]]:
        """Anonymous tests must never reach this endpoint."""
        self.calls.append(("fav_folders", mid))
        raise AssertionError("favorite folders must not be requested anonymously")

    async def close(self) -> None:
        """Nothing to close: the HTTP session is shared."""
        self.calls.append(("close",))


async def make_provider(
    *,
    client: FakeClient | None = None,
    overrides: dict[str, Any] | None = None,
    setup_data: dict[str, Any] | None = None,
    raw_values: dict[str, Any] | None = None,
    http_session: Any = None,
) -> tuple[BilibiliProvider, FakeMass]:
    """Build a provider with resolved config entries and an injected client."""
    config = ProviderConfig(
        type=ProviderType.MUSIC,
        domain="bilibili",
        instance_id=PROVIDER,
        values={},
    )
    mass = FakeMass(
        http_session=http_session,
        setup_data=setup_data,
        raw_values=raw_values,
    )
    provider = BilibiliProvider(
        mass,
        MANIFEST,
        config,
        {
            ProviderFeature.SEARCH,
            ProviderFeature.LIBRARY_PLAYLISTS,
            ProviderFeature.RECOMMENDATIONS,
            ProviderFeature.BROWSE,
        },
    )
    for entry in await provider.get_config_entries():
        copied = copy.deepcopy(entry)
        if overrides and entry.key in overrides:
            copied.value = overrides[entry.key]
        config.values[copied.key] = copied
    if client is not None:
        provider._client = client
    return provider, mass


# ---------------------------------------------------------------------------
# payload builders
# ---------------------------------------------------------------------------
def search_item(
    bvid: str,
    title: str,
    author: str,
    duration: str | int,
    play: int,
    mid: str = "42",
) -> dict[str, Any]:
    """Build a search result entry."""
    return {
        "bvid": bvid,
        "aid": 1,
        "mid": mid,
        "author": author,
        "title": title,
        "duration": duration,
        "pic": "//i0.hdslb.com/bfs/archive/cover.jpg",
        "pubdate": 1,
        "play": play,
        "type": "video",
    }


def audio_entry(
    stream_id: int,
    host: str,
    bandwidth: int = 100_000,
    *,
    deadline: int | None = None,
    backups: list[str] | None = None,
    mime_type: str = "audio/mp4",
    codecs: str = "mp4a.40.2",
) -> dict[str, Any]:
    """Build a DASH audio candidate."""
    url = f"https://{host}.example/audio.m4s"
    if deadline is not None:
        url = f"{url}?deadline={deadline}"
    return {
        "id": stream_id,
        "baseUrl": url,
        "backupUrl": backups or [],
        "bandwidth": bandwidth,
        "mimeType": mime_type,
        "codecs": codecs,
    }


def dash_payload(
    *,
    audio: list[dict[str, Any]] | None = None,
    dolby: list[dict[str, Any]] | dict[str, Any] | None = None,
    flac: list[dict[str, Any]] | dict[str, Any] | None = None,
    duration: int = 213,
) -> dict[str, Any]:
    """Build a playurl payload with the requested candidate sets."""
    return {
        "dash": {
            "duration": duration,
            "audio": audio or [],
            "dolby": {"audio": dolby},
            "flac": {"audio": flac},
        }
    }


# ---------------------------------------------------------------------------
# configuration / setup
# ---------------------------------------------------------------------------
async def test_config_entries_match_contract() -> None:
    """The option keys, defaults and quality choices follow the contract."""
    provider, _ = await make_provider()
    entries = await provider.get_config_entries()
    by_key = {entry.key: entry for entry in entries}

    assert [entry.key for entry in entries[:6]] == [
        constants.CONF_QUALITY,
        constants.CONF_MIN_DURATION,
        constants.CONF_MAX_DURATION,
        constants.CONF_FAVORITES_MUSIC_ONLY,
        constants.CONF_SEARCH_LIMIT,
        constants.CONF_VERIFY_STREAM_URL,
    ]
    quality = by_key[constants.CONF_QUALITY]
    assert quality.default_value == constants.QUALITY_AUTO
    assert [option.value for option in quality.options] == list(constants.QUALITY_OPTIONS)

    assert by_key[constants.CONF_MIN_DURATION].default_value == constants.DEFAULT_MIN_DURATION
    assert by_key[constants.CONF_MAX_DURATION].default_value == constants.DEFAULT_MAX_DURATION
    assert by_key[constants.CONF_FAVORITES_MUSIC_ONLY].default_value is True
    assert by_key[constants.CONF_SEARCH_LIMIT].default_value == constants.DEFAULT_SEARCH_LIMIT
    assert by_key[constants.CONF_VERIFY_STREAM_URL].default_value is True

    sessdata = by_key[constants.CONF_SESSDATA_MANUAL]
    assert sessdata.type == ConfigEntryType.SECURE_STRING
    assert by_key[CONF_ENTRY_UNOFFICIAL_PROVIDER.key] is CONF_ENTRY_UNOFFICIAL_PROVIDER


async def test_setup_returns_provider_with_expected_features() -> None:
    """setup() builds a provider that supports exactly the contract features."""
    config = ProviderConfig(
        type=ProviderType.MUSIC,
        domain="bilibili",
        instance_id=PROVIDER,
        values={},
    )
    provider = await setup(FakeMass(), MANIFEST, config)

    assert isinstance(provider, BilibiliProvider)
    assert provider.supported_features == {
        ProviderFeature.SEARCH,
        ProviderFeature.LIBRARY_PLAYLISTS,
        ProviderFeature.RECOMMENDATIONS,
        ProviderFeature.BROWSE,
    }


# ---------------------------------------------------------------------------
# handle_async_init
# ---------------------------------------------------------------------------
class InitFakeClient:
    """Client double used only while testing handle_async_init."""

    nav_result: ClassVar[dict[str, Any]] = {}
    nav_error: ClassVar[Exception | None] = None
    spi_result: ClassVar[dict[str, str]] = {"b_3": "buvid3", "b_4": "buvid4"}
    instances: ClassVar[list[InitFakeClient]] = []

    def __init__(self, mass: Any, credential: BilibiliCredential, logger: Any = None) -> None:
        self.mass = mass
        self.credential = credential
        self.logger = logger
        self.nav_calls = 0
        self.spi_calls = 0
        InitFakeClient.instances.append(self)

    async def nav(self) -> dict[str, Any]:
        """Return the configured nav payload."""
        self.nav_calls += 1
        if InitFakeClient.nav_error is not None:
            raise InitFakeClient.nav_error
        return dict(InitFakeClient.nav_result)

    async def spi(self) -> dict[str, str]:
        """Return the configured device cookies."""
        self.spi_calls += 1
        return dict(InitFakeClient.spi_result)

    async def close(self) -> None:
        """Nothing to close."""


@pytest.fixture
def init_client(monkeypatch: pytest.MonkeyPatch) -> type[InitFakeClient]:
    """Replace the real HTTP client used by handle_async_init."""
    monkeypatch.setattr("bilibili.BilibiliClient", InitFakeClient)
    InitFakeClient.instances = []
    InitFakeClient.nav_result = {}
    InitFakeClient.nav_error = None
    InitFakeClient.spi_result = {"b_3": "buvid3", "b_4": "buvid4"}
    return InitFakeClient


async def test_init_anonymous_fetches_buvid(init_client: type[InitFakeClient]) -> None:
    """Anonymous startup fetches the buvid fingerprint exactly once."""
    provider, _ = await make_provider()
    await provider.handle_async_init()

    client = provider._client
    assert client is not None
    assert client.nav_calls == 0
    assert client.spi_calls == 1
    assert client.credential.has_login is False
    assert client.credential.buvid3 == "buvid3"
    assert provider._mid == 0
    assert provider._user_name == ""


async def test_init_valid_credential_records_user(
    init_client: type[InitFakeClient],
) -> None:
    """A valid nav response records user name and mid without an spi call."""
    credential = BilibiliCredential(sessdata="sess", bili_jct="jct", dedeuserid="42")
    provider, _ = await make_provider(
        setup_data={constants.CONF_CREDENTIAL_JSON: credential.to_json()}
    )
    init_client.nav_result = {"isLogin": True, "mid": 42, "uname": "Alice"}

    await provider.handle_async_init()

    client = provider._client
    assert client is not None
    assert client.nav_calls == 1
    assert client.spi_calls == 0
    assert provider._user_name == "Alice"
    assert provider._mid == 42
    assert client.credential.has_login is True


async def test_init_invalid_credential_degrades_to_anonymous(
    init_client: type[InitFakeClient],
) -> None:
    """A logged-out nav response never blocks loading; it degrades to anonymous."""
    credential = BilibiliCredential(sessdata="sess", dedeuserid="42")
    provider, _ = await make_provider(
        setup_data={constants.CONF_CREDENTIAL_JSON: credential.to_json()}
    )
    init_client.nav_result = {"isLogin": False}

    await provider.handle_async_init()

    client = provider._client
    assert client is not None
    assert client.credential.has_login is False
    assert client.credential.sessdata == ""
    assert client.spi_calls == 1
    assert provider._user_name == ""
    assert provider._mid == 0


async def test_init_nav_api_error_degrades_to_anonymous(
    init_client: type[InitFakeClient],
) -> None:
    """A rejected nav call logs a warning and continues anonymously."""
    credential = BilibiliCredential(sessdata="sess", dedeuserid="42")
    provider, _ = await make_provider(
        setup_data={constants.CONF_CREDENTIAL_JSON: credential.to_json()}
    )
    init_client.nav_error = BiliApiError(-101, "not logged in", "/x/web-interface/nav")

    await provider.handle_async_init()

    client = provider._client
    assert client is not None
    assert client.credential.has_login is False
    assert client.spi_calls == 1


async def test_init_network_error_keeps_saved_credential(
    init_client: type[InitFakeClient],
) -> None:
    """A transient network failure is not proof that the login is invalid."""
    credential = BilibiliCredential(sessdata="sess", dedeuserid="42")
    provider, _ = await make_provider(
        setup_data={constants.CONF_CREDENTIAL_JSON: credential.to_json()}
    )
    init_client.nav_error = ResourceTemporarilyUnavailable("timeout")

    await provider.handle_async_init()

    client = provider._client
    assert client is not None
    assert client.credential.has_login is True
    assert client.spi_calls == 0


async def test_init_manual_sessdata_backfills_mid(
    init_client: type[InitFakeClient],
) -> None:
    """A manually pasted SESSDATA gets DedeUserID and user name from nav."""
    provider, _ = await make_provider(
        raw_values={constants.CONF_SESSDATA_MANUAL: "manual-sessdata"}
    )
    init_client.nav_result = {"isLogin": True, "mid": 77, "uname": "Bob"}

    await provider.handle_async_init()

    client = provider._client
    assert client is not None
    assert client.credential.sessdata == "manual-sessdata"
    assert client.credential.dedeuserid == "77"
    assert client.credential.has_login is True
    assert provider._mid == 77
    assert provider._user_name == "Bob"
    assert client.spi_calls == 0


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
async def test_search_filters_dedupes_sorts_and_truncates() -> None:
    """Search keeps tracks only, filters durations, dedupes and sorts by plays."""
    client = FakeClient(
        search_items=[
            search_item("BV1", "晴天 - 周杰伦", "UP", "4:30", 100),
            search_item("BV2", "晴天 - 周杰伦", "UP", "4:30", 900),
            search_item("BV3", "七里香 - 周杰伦", "UP", "4:59", 500),
            search_item("BV4", "超长合集", "UP", "20:00", 5000),
            search_item("BV5", "短切片", "UP", "0:30", 4000),
            search_item("BV6", "未知时长", "UP", "", 10),
        ]
    )
    provider, _ = await make_provider(client=client)

    result = await provider.search("周杰伦", [MediaType.TRACK], limit=3)

    assert [track.item_id for track in result.tracks] == ["BV2", "BV3", "BV6"]
    assert client.calls[0][0] == "search"
    # The provider asks for enough raw results to survive filtering/dedupe.
    assert client.calls[0][1:] == ("周杰伦", 1, 20)


async def test_favorites_music_filter_drops_non_music_video() -> None:
    """The favorites filter drops videos outside the music zones."""
    client = FakeClient(
        view_data={
            "bvid": "BV1",
            "aid": 1,
            "title": "非音乐内容",
            "duration": 300,
            "tid": 17,
            "pages": [
                {"cid": 111, "page": 1, "part": "P1", "duration": 100},
                {"cid": 222, "page": 2, "part": "P2", "duration": 200},
            ],
            "owner": {"mid": 42, "name": "UP"},
            "stat": {"view": 10},
        }
    )
    provider, _ = await make_provider(client=client)
    track = parsers.parse_search_item(search_item("BV1", "非音乐内容", "UP", 300, 10), PROVIDER)

    assert track is not None
    assert await provider._expand_multipart_tracks([track], music_only=True) == []


async def test_search_by_bvid_returns_every_page() -> None:
    """A direct BV lookup skips keyword search and returns all pages."""
    view_data = {
        "bvid": "BV1AWTY6HEPr",
        "aid": 1,
        "title": "FGO OST 8",
        "pic": "//i0.hdslb.com/bfs/archive/cover.jpg",
        "duration": 300,
        "pubdate": 1,
        "owner": {"mid": 42, "name": "UP"},
        "stat": {"view": 10},
        "pages": [
            {"cid": 111, "page": 1, "part": "【Disc1】01. Ground", "duration": 180},
            {"cid": 222, "page": 2, "part": "02. Proof", "duration": 154},
        ],
    }
    client = FakeClient(view_data=view_data)
    provider, _ = await make_provider(client=client)

    result = await provider.search("BV1AWTY6HEPr", [MediaType.TRACK], limit=5)

    assert [track.item_id for track in result.tracks] == [
        "BV1AWTY6HEPr#111",
        "BV1AWTY6HEPr#222",
    ]
    assert result.tracks[0].name == "【Disc1】01. Ground"
    assert result.tracks[0].track_number == 1


async def test_search_expands_multipart_video() -> None:
    """A multi-part search hit becomes one track per page."""
    view_data = {
        "bvid": "BV1",
        "aid": 1,
        "title": "FGO OST Collection",
        "pic": "//i0.hdslb.com/bfs/archive/cover.jpg",
        "duration": 300,
        "pubdate": 1,
        "owner": {"mid": 42, "name": "UP"},
        "stat": {"view": 10},
        "pages": [
            {"cid": 111, "page": 1, "part": "Disc 1 - Track 1", "duration": 100},
            {"cid": 222, "page": 2, "part": "Disc 1 - Track 2", "duration": 200},
        ],
    }
    client = FakeClient(
        search_items=[search_item("BV1", "FGO OST Collection", "UP", "190:22", 100)],
        view_data=view_data,
    )
    provider, _ = await make_provider(client=client)

    result = await provider.search("FGO OST", [MediaType.TRACK], limit=10)

    assert [track.item_id for track in result.tracks] == ["BV1#111", "BV1#222"]
    assert [track.name for track in result.tracks] == ["Disc 1 - Track 1", "Disc 1 - Track 2"]
    assert [track.track_number for track in result.tracks] == [1, 2]


async def test_search_honours_search_limit_config() -> None:
    """search_limit caps the number of results even for a larger MA limit."""
    client = FakeClient(
        search_items=[
            search_item(f"BV{index}", f"歌曲 {index}", "UP", 270, index) for index in range(5)
        ]
    )
    provider, _ = await make_provider(client=client, overrides={constants.CONF_SEARCH_LIMIT: 2})

    result = await provider.search("歌曲", [MediaType.TRACK], limit=10)

    assert len(result.tracks) == 2


async def test_search_ignores_non_track_media_types() -> None:
    """Artists/albums are not searched at all."""
    client = FakeClient()
    provider, _ = await make_provider(client=client)

    result = await provider.search("周杰伦", [MediaType.ARTIST, MediaType.ALBUM])

    assert list(result.tracks) == []
    assert client.calls == []


async def test_search_empty_query_does_not_call_api() -> None:
    """An empty query returns no results without a request."""
    client = FakeClient()
    provider, _ = await make_provider(client=client)

    result = await provider.search("   ", [MediaType.TRACK])

    assert list(result.tracks) == []
    assert client.calls == []


# ---------------------------------------------------------------------------
# get_track / get_artist
# ---------------------------------------------------------------------------
def multi_part_view() -> dict[str, Any]:
    """Build a two-part view payload."""
    return {
        "bvid": "BV1multi",
        "aid": 1,
        "title": "两首歌的合集",
        "pic": "//i0.hdslb.com/bfs/archive/cover.jpg",
        "duration": 500,
        "pubdate": 1,
        "owner": {"mid": 42, "name": "UP"},
        "pages": [
            {"cid": 111, "page": 1, "part": "第一首", "duration": 200},
            {"cid": 222, "page": 2, "part": "第二首", "duration": 300},
        ],
        "stat": {"view": 10},
    }


async def test_get_track_uses_page_part_and_caches_view() -> None:
    """A bvid#cid lookup uses the matching page title and caches the view."""
    client = FakeClient(view_data=multi_part_view())
    provider, _ = await make_provider(client=client)

    track = await provider.get_track("BV1multi#222")

    assert track.item_id == "BV1multi#222"
    assert track.name == "第二首"
    assert track.duration == 300
    assert {mapping.item_id for mapping in track.provider_mappings} == {"BV1multi#222"}

    await provider.get_track("BV1multi#222")
    assert client.view_calls == 1


async def test_get_track_rejects_empty_id() -> None:
    """An empty provider track id is a not-found error."""
    provider, _ = await make_provider(client=FakeClient())
    with pytest.raises(MediaNotFoundError):
        await provider.get_track("   ")


async def test_get_artist_uses_remembered_name_and_fallback() -> None:
    """Artist lookups come from memory, with a readable fallback name."""
    client = FakeClient(
        search_items=[search_item("BV1", "晴天 - 周杰伦", "索尼音乐", 270, 10, mid="486906719")]
    )
    provider, _ = await make_provider(client=client)
    await provider.search("晴天", [MediaType.TRACK])

    known = await provider.get_artist("486906719")
    assert known.name == "索尼音乐"
    assert known.provider_mappings

    unknown = await provider.get_artist("999")
    assert unknown.name == "Bilibili UP 主 999"


# ---------------------------------------------------------------------------
# get_stream_details
# ---------------------------------------------------------------------------
async def test_stream_default_quality_picks_flac() -> None:
    """auto quality prefers FLAC over Dolby and AAC."""
    deadline = int(time.time()) + 900
    flac = audio_entry(
        30251,
        "cdn-flac",
        900_000,
        deadline=deadline,
        mime_type="audio/flac",
        codecs="flac",
    )
    dolby = audio_entry(30250, "cdn-dolby", 448_000, deadline=deadline, codecs="ec-3")
    aac = audio_entry(30280, "cdn-aac", 203_786, deadline=deadline)
    client = FakeClient(playurl_data=dash_payload(audio=[aac], dolby=[dolby], flac=flac))
    provider, _ = await make_provider(
        client=client, overrides={constants.CONF_VERIFY_STREAM_URL: False}
    )

    details = await provider.get_stream_details("BV1#123", MediaType.TRACK)

    assert details.path == flac["baseUrl"]
    assert details.audio_format.content_type == ContentType.FLAC
    assert details.stream_type == StreamType.HTTP
    assert details.can_seek is True
    assert details.allow_seek is True
    assert details.extra_input_args == constants.FFMPEG_HEADERS
    assert details.provider == PROVIDER
    assert details.item_id == "BV1#123"


@pytest.mark.parametrize(
    ("quality", "expected_id", "expected_type"),
    [
        ("dolby", 30250, ContentType.EAC3),
        ("aac192", 30280, ContentType.M4A),
        ("aac132", 30232, ContentType.M4A),
        ("aac64", 30216, ContentType.M4A),
    ],
)
async def test_stream_quality_ceiling(
    quality: str, expected_id: int, expected_type: ContentType
) -> None:
    """The quality option caps the selected stream id."""
    audio = [
        audio_entry(30216, "cdn-64", 43_962),
        audio_entry(30232, "cdn-132", 102_931),
        audio_entry(30280, "cdn-192", 203_786),
    ]
    dolby = audio_entry(30250, "cdn-dolby", 448_000, codecs="ec-3")
    flac = audio_entry(30251, "cdn-flac", 900_000, mime_type="audio/flac", codecs="flac")
    client = FakeClient(playurl_data=dash_payload(audio=audio, dolby=[dolby], flac=flac))
    provider, _ = await make_provider(
        client=client,
        overrides={
            constants.CONF_QUALITY: quality,
            constants.CONF_VERIFY_STREAM_URL: False,
        },
    )

    details = await provider.get_stream_details("BV1#123", MediaType.TRACK)

    expected = next(entry for entry in [*audio, dolby, flac] if entry["id"] == expected_id)
    assert details.path == expected["baseUrl"]
    assert details.audio_format.content_type == expected_type


async def test_stream_resolves_cid_from_view_when_missing() -> None:
    """Without a cid the provider asks the view endpoint for the first page."""
    client = FakeClient(
        view_data=multi_part_view(),
        playurl_data=dash_payload(audio=[audio_entry(30280, "cdn-aac")]),
    )
    provider, _ = await make_provider(
        client=client, overrides={constants.CONF_VERIFY_STREAM_URL: False}
    )

    details = await provider.get_stream_details("BV1multi", MediaType.TRACK)

    assert details.path == "https://cdn-aac.example/audio.m4s"
    assert ("playurl", "BV1multi", None, 111) in client.calls


async def test_stream_handles_avid_and_single_dict_flac() -> None:
    """av ids and a dict-shaped dash.flac.audio both work."""
    flac = audio_entry(30251, "cdn-flac", 900_000, mime_type="audio/flac", codecs="flac")
    client = FakeClient(playurl_data=dash_payload(flac=flac))
    provider, _ = await make_provider(
        client=client, overrides={constants.CONF_VERIFY_STREAM_URL: False}
    )

    details = await provider.get_stream_details("av80433022#137649199", MediaType.TRACK)

    assert details.path == flac["baseUrl"]
    assert ("playurl", None, 80433022, 137649199) in client.calls


async def test_stream_probe_falls_back_to_backup_url() -> None:
    """A failing primary CDN probe switches to the first healthy backup."""
    deadline = int(time.time()) + 1000
    primary = audio_entry(30280, "cdn-primary", deadline=deadline)
    backup = audio_entry(30280, "cdn-backup", deadline=deadline)
    primary["backupUrl"] = [backup["baseUrl"]]
    http = RecordingHTTPSession([403, 206])
    client = FakeClient(playurl_data=dash_payload(audio=[primary]))
    provider, _ = await make_provider(client=client, http_session=http)

    details = await provider.get_stream_details("BV1#123", MediaType.TRACK)

    assert details.path == backup["baseUrl"]
    assert len(http.requests) == 2
    for _, kwargs in http.requests:
        assert kwargs["headers"]["Range"] == "bytes=0-1023"
    # deadline - now - 60, within the allowed bounds.
    assert 300 <= details.expiration <= 940


async def test_stream_probe_failure_returns_primary_url() -> None:
    """When every probe fails the primary URL is returned for ffmpeg to try."""
    primary = audio_entry(30280, "cdn-primary")
    backup = audio_entry(30280, "cdn-backup")
    primary["backupUrl"] = [backup["baseUrl"]]
    http = RecordingHTTPSession([500, 404])
    client = FakeClient(playurl_data=dash_payload(audio=[primary]))
    provider, _ = await make_provider(client=client, http_session=http)

    details = await provider.get_stream_details("BV1#123", MediaType.TRACK)

    assert details.path == primary["baseUrl"]
    assert len(http.requests) == 2


async def test_stream_expiration_is_clamped() -> None:
    """Expiration uses the URL deadline and is clamped to 300..7200 seconds."""
    far = audio_entry(30280, "cdn-far", deadline=int(time.time()) + 100_000)
    client = FakeClient(playurl_data=dash_payload(audio=[far]))
    provider, _ = await make_provider(
        client=client, overrides={constants.CONF_VERIFY_STREAM_URL: False}
    )

    details = await provider.get_stream_details("BV1#123", MediaType.TRACK)
    assert details.expiration == constants.STREAM_EXPIRATION_MAX

    past = audio_entry(30280, "cdn-past", deadline=int(time.time()) - 100)
    client.playurl_data = dash_payload(audio=[past])
    details = await provider.get_stream_details("BV1#123", MediaType.TRACK)
    assert details.expiration == constants.STREAM_EXPIRATION_MIN


async def test_stream_without_verification_does_not_probe() -> None:
    """verify_stream_url=false skips the CDN request entirely."""
    http = RecordingHTTPSession()
    entry = audio_entry(30280, "cdn-aac")
    client = FakeClient(playurl_data=dash_payload(audio=[entry]))
    provider, _ = await make_provider(
        client=client,
        http_session=http,
        overrides={constants.CONF_VERIFY_STREAM_URL: False},
    )

    details = await provider.get_stream_details("BV1#123", MediaType.TRACK)

    assert details.path == entry["baseUrl"]
    assert http.requests == []


@pytest.mark.parametrize(
    "playurl_data",
    [
        {},
        {"dash": {}},
        {"dash": {"audio": [], "dolby": {"audio": None}, "flac": None}},
        {"dash": {"audio": [{"id": 30280, "bandwidth": 1}]}},
    ],
)
async def test_stream_without_audio_is_unplayable(playurl_data: dict[str, Any]) -> None:
    """A missing DASH payload or empty candidate list raises UnplayableMediaError."""
    client = FakeClient(playurl_data=playurl_data)
    provider, _ = await make_provider(
        client=client, overrides={constants.CONF_VERIFY_STREAM_URL: False}
    )

    with pytest.raises(UnplayableMediaError):
        await provider.get_stream_details("BV1#123", MediaType.TRACK)


async def test_stream_without_cid_or_pages_is_unplayable() -> None:
    """A video payload without any page cannot be played."""
    client = FakeClient(view_data={"bvid": "BV1", "pages": []})
    provider, _ = await make_provider(client=client)

    with pytest.raises(UnplayableMediaError):
        await provider.get_stream_details("BV1", MediaType.TRACK)
    assert not any(call[0] == "playurl" for call in client.calls)


async def test_stream_rejects_non_track_media_type() -> None:
    """Only tracks can produce Bilibili stream details."""
    provider, _ = await make_provider(client=FakeClient())
    with pytest.raises(MediaNotFoundError):
        await provider.get_stream_details("BV1#123", MediaType.ARTIST)


# ---------------------------------------------------------------------------
# library playlists
# ---------------------------------------------------------------------------
async def test_anonymous_library_playlists_are_empty() -> None:
    """Anonymous access yields no favorite folders and makes no request."""
    client = FakeClient(BilibiliCredential())
    provider, _ = await make_provider(client=client)

    playlists = [playlist async for playlist in provider.get_library_playlists()]

    assert playlists == []
    assert client.calls == []


async def test_unload_drops_client_and_caches() -> None:
    """unload closes the client and clears the in-memory caches."""
    client = FakeClient()
    provider, _ = await make_provider(client=client)

    await provider.unload()

    assert provider._client is None
    assert ("close",) in client.calls


# ---------------------------------------------------------------------------
# error translation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("code", "backoff"),
    [(-352, 30), (-412, 60)],
)
async def test_search_rate_limit_errors(code: int, backoff: int) -> None:
    """Risk-control codes map to a transient error with the documented backoff."""
    client = FakeClient()
    client.search_error = BiliApiError(code, "rate limited", "/search")
    provider, _ = await make_provider(client=client)

    with pytest.raises(ResourceTemporarilyUnavailable) as info:
        await provider.search("晴天", [MediaType.TRACK], limit=3)
    assert info.value.backoff_time == backoff


@pytest.mark.parametrize("code", [-404, 62002, 62004])
async def test_media_not_found_errors(code: int) -> None:
    """Deleted/private videos map to MediaNotFoundError."""
    client = FakeClient()
    client.view_error = BiliApiError(code, "not found", "/x/web-interface/view")
    provider, _ = await make_provider(client=client)

    with pytest.raises(MediaNotFoundError):
        await provider.get_track("BV1")


@pytest.mark.parametrize("code", [87007, 87008])
async def test_membership_only_streams_are_unplayable(code: int) -> None:
    """Charged/members-only videos map to UnplayableMediaError."""
    client = FakeClient()
    client.playurl_error = BiliApiError(code, "vip only", "/x/player/playurl")
    provider, _ = await make_provider(
        client=client, overrides={constants.CONF_VERIFY_STREAM_URL: False}
    )

    with pytest.raises(UnplayableMediaError):
        await provider.get_stream_details("BV1#123", MediaType.TRACK)


async def test_unknown_api_error_is_temporarily_unavailable() -> None:
    """An unrecognized API code is retried rather than treated as missing media."""
    client = FakeClient()
    client.search_error = BiliApiError(-500, "server error", "/search")
    provider, _ = await make_provider(client=client)

    with pytest.raises(ResourceTemporarilyUnavailable):
        await provider.search("晴天", [MediaType.TRACK])


async def test_network_error_passes_through() -> None:
    """The client already raises MA errors for network failures."""
    client = FakeClient()
    client.search_error = ResourceTemporarilyUnavailable("timeout", backoff_time=5)
    provider, _ = await make_provider(client=client)

    with pytest.raises(ResourceTemporarilyUnavailable) as info:
        await provider.search("晴天", [MediaType.TRACK])
    assert info.value.backoff_time == 5


# ---------------------------------------------------------------------------
# recommendations
# ---------------------------------------------------------------------------
async def test_recommendations_expose_three_rows() -> None:
    """The recommendation rows mirror the catalog descriptors."""
    provider, _ = await make_provider(client=FakeClient())
    rows = await provider.get_recommendations()

    assert [row.item_id for row in rows] == ["rank_music", "new_music", "region_music"]
    assert all(row.provider == PROVIDER for row in rows)
