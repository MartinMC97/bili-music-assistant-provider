"""Unit tests for Bilibili live room support (fully offline, fake client).

Live rooms are reached by room number only, come back as ``Radio`` items and are
streamed through ffmpeg as a realtime, unseekable HTTP source.  The fake client
serves the recorded ``tests/fixtures/live_*.json`` payloads so no test touches
the network.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from bilibili import BilibiliProvider, live, parsers
from bilibili.auth import BilibiliCredential
from bilibili.client import BiliApiError
from music_assistant_models.config_entries import ProviderConfig
from music_assistant_models.enums import (
    ContentType,
    MediaType,
    ProviderFeature,
    ProviderStage,
    ProviderType,
    StreamType,
)
from music_assistant_models.errors import MediaNotFoundError, UnplayableMediaError

PROVIDER = "bilibili_live_test"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
MANIFEST = SimpleNamespace(
    type=ProviderType.MUSIC,
    domain="bilibili",
    name="Bilibili",
    stage=ProviderStage.ALPHA,
)

FLV_URL = (
    "https://d1--cn-flv.bilivideo.com"
    "/live-bvc/132889/live_50329118_fixture.flv?expires=1700000000&sign=deadbeef&qn=250"
)
FLV_BACKUP = (
    "https://d1--cn-flv-b.bilivideo.com"
    "/live-bvc/132889/live_50329118_fixture.flv?expires=1700000000&sign=deadbeef&qn=250"
)


# ---------------------------------------------------------------------------
# fixtures / fake collaborators
# ---------------------------------------------------------------------------
def load_fixture(name: str) -> dict[str, Any]:
    """Return a fresh copy of a recorded live API payload."""
    raw = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return copy.deepcopy(raw)


def room_info() -> dict[str, Any]:
    """Return the recorded room info ``data`` object."""
    return load_fixture("live_room_info")["data"]


def playurl_info() -> dict[str, Any]:
    """Return the recorded playurl ``data`` object."""
    return load_fixture("live_playurl")["data"]


class NoNetworkSession:
    """Fail loudly if any test reaches the network."""

    def get(self, *args: Any, **kwargs: Any) -> Any:
        """Reject every request."""
        raise AssertionError("live tests must not perform HTTP requests")


class FakeMassConfig:
    """The slice of ``mass.config`` the provider needs."""

    def get(self, key: str, default: Any = None) -> Any:
        """Return an empty setup payload and otherwise the default."""
        text = str(key)
        if text.endswith("/setup_data") or text == "providers":
            return {}
        return default

    def decrypt_string(self, value: str) -> str:
        """Return stored secrets as plain text."""
        return value

    def get_raw_provider_config_value(self, instance_id: str, key: str) -> Any:
        """Return None so config entries keep their defaults."""
        return None


class FakeMass:
    """Minimal ``MusicAssistant`` stand-in with all network access disabled."""

    def __init__(self) -> None:
        """Build the stand-in."""
        self.cache = None
        self.http_session = NoNetworkSession()
        self.config = FakeMassConfig()


class FakeClient:
    """Fake Bilibili client serving canned live payloads."""

    def __init__(
        self,
        info: dict[str, Any] | None = None,
        play: dict[str, Any] | None = None,
        info_error: BiliApiError | None = None,
    ) -> None:
        """Store payloads (or an error) and record calls."""
        self.info = room_info() if info is None else info
        self.play = playurl_info() if play is None else play
        self.info_error = info_error
        self.credential = BilibiliCredential()
        self.info_calls: list[str] = []
        self.play_calls: list[str] = []
        self.search_calls: list[str] = []

    async def live_room_info(self, room_id: str) -> dict[str, Any]:
        """Return the canned room info payload."""
        self.info_calls.append(room_id)
        if self.info_error is not None:
            raise self.info_error
        return self.info

    async def live_playurl(self, room_id: str) -> dict[str, Any]:
        """Return the canned playurl payload."""
        self.play_calls.append(room_id)
        return self.play

    async def search_video(self, keyword: str, page: int = 1, page_size: int = 20) -> list:
        """Record the call and return no music results."""
        self.search_calls.append(keyword)
        return []


async def make_provider(client: FakeClient) -> BilibiliProvider:
    """Build a provider with an injected fake client."""
    config = ProviderConfig(
        type=ProviderType.MUSIC,
        domain="bilibili",
        instance_id=PROVIDER,
        values={},
    )
    provider = BilibiliProvider(FakeMass(), MANIFEST, config, {ProviderFeature.SEARCH})
    for entry in await provider.get_config_entries():
        config.values[entry.key] = copy.deepcopy(entry)
    provider._client = client
    return provider


# ---------------------------------------------------------------------------
# query / item id parsing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("6", "6"),
        ("  21452505 ", "21452505"),
        ("live:6", "6"),
        ("https://live.bilibili.com/6?broadcast_type=0", "6"),
        ("https://live.bilibili.com/h5/21452505", "21452505"),
        ("12345678901", None),  # too long to be a room number
        ("周杰伦", None),
        ("BV1xx411c7mD", None),
        ("live:abc", None),
        ("", None),
    ],
)
def test_room_id_from_query(query: str, expected: str | None) -> None:
    """Only room numbers and live URLs are treated as live queries."""
    assert live.room_id_from_query(query) == expected


@pytest.mark.parametrize(
    ("item_id", "expected"),
    [("live:21452505", "21452505"), ("live:6", "6"), ("BV1xx411c7mD", None), ("live:", None)],
)
def test_room_id_from_item_id(item_id: str, expected: str | None) -> None:
    """Item ids carry the room id behind the ``live:`` prefix."""
    assert live.room_id_from_item_id(item_id) == expected


# ---------------------------------------------------------------------------
# payload parsing
# ---------------------------------------------------------------------------
def test_parse_live_room_builds_radio() -> None:
    """A room payload becomes a radio item pointing at the live room."""
    radio = parsers.parse_live_room(room_info(), PROVIDER)

    assert radio is not None
    assert radio.item_id == "live:7734200"
    assert radio.provider == PROVIDER
    assert radio.name == "【预告】9号6点BLG vs T1"
    assert radio.media_type == MediaType.RADIO
    assert "直播中" in (radio.metadata.description or "")
    assert "赛事" in (radio.metadata.description or "")
    assert radio.metadata.images[0].path.startswith("https://i0.hdslb.com/")
    mapping = next(iter(radio.provider_mappings))
    assert mapping.url == "https://live.bilibili.com/7734200"


def test_parse_live_room_rejects_payload_without_room_id() -> None:
    """Malformed payloads are skipped instead of raising."""
    assert parsers.parse_live_room({}, PROVIDER) is None
    assert parsers.parse_live_room({"room_id": "abc"}, PROVIDER) is None


def test_pick_live_stream_prefers_flv_over_hls_and_hevc() -> None:
    """The FLV/AVC rendition wins and keeps the alternative CDN host as backup."""
    stream = parsers.pick_live_stream(playurl_info())

    assert stream is not None
    assert stream.url == FLV_URL
    assert stream.backups == [FLV_BACKUP]
    assert (stream.protocol, stream.format, stream.codec) == ("http_stream", "flv", "avc")
    assert stream.qn == 250


def test_pick_live_stream_returns_none_without_playurl() -> None:
    """A room without playurl info is not playable."""
    assert parsers.pick_live_stream({}) is None
    assert parsers.pick_live_stream({"playurl_info": {"playurl": {"stream": []}}}) is None


# ---------------------------------------------------------------------------
# provider wiring
# ---------------------------------------------------------------------------
async def test_search_room_number_returns_radio_only() -> None:
    """A room number yields one radio result and never triggers a music search."""
    client = FakeClient()
    provider = await make_provider(client)

    results = await provider.search("6", [MediaType.TRACK, MediaType.RADIO], limit=5)

    assert results.tracks == []
    assert [item.item_id for item in results.radio] == ["live:7734200"]
    assert client.info_calls == ["6"]
    assert client.search_calls == []


async def test_search_music_query_never_calls_live_api() -> None:
    """An ordinary query stays on the music search path."""
    client = FakeClient()
    provider = await make_provider(client)

    results = await provider.search("周杰伦", [MediaType.TRACK, MediaType.RADIO])

    assert results.radio == []
    assert client.info_calls == []
    assert client.search_calls == ["周杰伦"]


async def test_search_unknown_room_falls_back_to_music() -> None:
    """A numeric query that is not a room still searches music."""
    client = FakeClient(info_error=BiliApiError(1, "未找到该房间", "/room/v1/Room/get_info"))
    provider = await make_provider(client)

    results = await provider.search("2024", [MediaType.TRACK, MediaType.RADIO])

    assert results.radio == []
    assert client.search_calls == ["2024"]


async def test_search_room_skipped_when_radio_not_requested() -> None:
    """Without the radio media type the live API is not queried at all."""
    client = FakeClient()
    provider = await make_provider(client)

    await provider.search("6", [MediaType.TRACK])

    assert client.info_calls == []
    assert client.search_calls == ["6"]


async def test_get_stream_details_returns_realtime_radio_stream() -> None:
    """Live playback is an unseekable, expiring, realtime HTTP radio stream."""
    client = FakeClient()
    provider = await make_provider(client)

    details = await provider.get_stream_details("live:7734200", MediaType.RADIO)

    assert details.media_type == MediaType.RADIO
    assert details.stream_type == StreamType.HTTP
    assert details.path == FLV_URL
    assert details.audio_format.content_type == ContentType.UNKNOWN
    assert details.duration is None
    assert details.can_seek is False
    assert details.allow_seek is False
    assert details.expiration == 300
    assert any("live.bilibili.com" in arg for arg in details.extra_input_args)
    assert client.play_calls == ["7734200"]


async def test_get_stream_details_rejects_offline_room() -> None:
    """An offline room (or its replay loop) is not streamable."""
    info = room_info()
    info["live_status"] = 0
    provider = await make_provider(FakeClient(info=info))

    with pytest.raises(MediaNotFoundError):
        await provider.get_stream_details("live:7734200", MediaType.RADIO)


async def test_get_stream_details_rejects_room_without_streams() -> None:
    """A payload without playable renditions raises an unplayable error."""
    provider = await make_provider(FakeClient(play={"playurl_info": {}}))

    with pytest.raises(UnplayableMediaError):
        await provider.get_stream_details("live:7734200", MediaType.RADIO)


async def test_get_radio_returns_live_room() -> None:
    """Radio items are resolvable by id for queue/library lookups."""
    client = FakeClient()
    provider = await make_provider(client)

    radio = await provider.get_radio("live:7734200")

    assert radio.item_id == "live:7734200"
    assert radio.name == "【预告】9号6点BLG vs T1"
    assert client.info_calls == ["7734200"]
