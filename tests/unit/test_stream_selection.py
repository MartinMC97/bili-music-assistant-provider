"""Stream selection tests for :meth:`BilibiliProvider.get_stream_details`.

A fake client serves the recorded ``tests/fixtures/playurl.json`` DASH payload, so
these tests never touch the network. The CDN probe is disabled explicitly; the
fake HTTP session fails the test if a probe is ever attempted.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from bilibili import BilibiliProvider, constants
from bilibili.auth import BilibiliCredential
from music_assistant_models.config_entries import ProviderConfig
from music_assistant_models.enums import (
    ContentType,
    MediaType,
    ProviderFeature,
    ProviderStage,
    ProviderType,
    StreamType,
)

PROVIDER = "bilibili_stream_selection"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
MANIFEST = SimpleNamespace(
    type=ProviderType.MUSIC,
    domain="bilibili",
    name="Bilibili",
    stage=ProviderStage.ALPHA,
)

#: A Hi-Res FLAC candidate that is not part of the recorded fixture.
FLAC_URL = "https://cdn.example.invalid/audio-30251.flac?deadline=9999999999"


# ---------------------------------------------------------------------------
# fixtures / fake collaborators
# ---------------------------------------------------------------------------
def load_playurl() -> dict[str, Any]:
    """Return a fresh copy of the recorded playurl ``data`` object."""
    raw = json.loads((FIXTURES / "playurl.json").read_text(encoding="utf-8"))
    return copy.deepcopy(raw["data"])


def audio_url(data: dict[str, Any], stream_id: int) -> str:
    """Return the base URL of a recorded DASH audio candidate."""
    for entry in data["dash"]["audio"]:
        if entry.get("id") == stream_id:
            return str(entry["baseUrl"])
    raise AssertionError(f"fixture has no audio candidate with id {stream_id}")


def add_flac(data: dict[str, Any]) -> dict[str, Any]:
    """Attach a dict-shaped ``dash.flac.audio`` candidate, as the task requires."""
    data["dash"]["flac"] = {
        "audio": {
            "id": 30251,
            "baseUrl": FLAC_URL,
            "backupUrl": [],
            "bandwidth": 1_411_200,
            "mimeType": "audio/flac",
            "codecs": "flac",
        }
    }
    return data


def overrides(**extra: Any) -> dict[str, Any]:
    """Base config overrides: probing is always disabled in these tests."""
    values: dict[str, Any] = {constants.CONF_VERIFY_STREAM_URL: False}
    values.update(extra)
    return values


class NoNetworkSession:
    """Fail loudly if stream selection ever performs an HTTP request."""

    def get(self, *args: Any, **kwargs: Any) -> Any:
        """Reject every request."""
        raise AssertionError("stream selection tests must not perform HTTP requests")


class FakeMassConfig:
    """The slice of ``mass.config`` the provider needs."""

    def __init__(self, raw_values: dict[str, Any] | None = None) -> None:
        """Store explicit raw config overrides."""
        self.raw_values = dict(raw_values or {})

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
        """Return an explicit override, or None to fall back to the entry default."""
        return self.raw_values.get(key)


class FakeMass:
    """Minimal ``MusicAssistant`` stand-in (config plus an HTTP session)."""

    def __init__(self, raw_values: dict[str, Any] | None = None) -> None:
        """Build the stand-in with all network access disabled."""
        self.cache = None
        self.http_session = NoNetworkSession()
        self.config = FakeMassConfig(raw_values)


class FakeClient:
    """Fake Bilibili client returning a canned playurl payload."""

    def __init__(self, playurl_data: dict[str, Any]) -> None:
        """Store the payload and record calls."""
        self.playurl_data = playurl_data
        self.credential = BilibiliCredential()
        self.playurl_calls: list[tuple[str | None, int | None, int]] = []

    async def playurl(
        self,
        *,
        bvid: str | None = None,
        avid: int | None = None,
        cid: int,
    ) -> dict[str, Any]:
        """Return the canned playurl payload."""
        self.playurl_calls.append((bvid, avid, cid))
        return self.playurl_data


async def make_provider(
    playurl_data: dict[str, Any],
    config_overrides: dict[str, Any] | None = None,
) -> tuple[BilibiliProvider, FakeClient]:
    """Build a provider with resolved config entries and an injected client."""
    config = ProviderConfig(
        type=ProviderType.MUSIC,
        domain="bilibili",
        instance_id=PROVIDER,
        values={},
    )
    mass = FakeMass(config_overrides)
    provider = BilibiliProvider(mass, MANIFEST, config, {ProviderFeature.SEARCH})
    for entry in await provider.get_config_entries():
        copied = copy.deepcopy(entry)
        if config_overrides and entry.key in config_overrides:
            copied.value = config_overrides[entry.key]
        config.values[copied.key] = copied

    client = FakeClient(playurl_data)
    provider._client = client
    return provider, client


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------
async def test_auto_selects_best_aac_from_fixture() -> None:
    """The default quality picks 30280 (192k AAC) from the recorded payload."""
    data = load_playurl()
    provider, client = await make_provider(data, overrides())

    details = await provider.get_stream_details("BV1fixture#1001", MediaType.TRACK)

    assert details.path == audio_url(data, 30280)
    assert details.audio_format.content_type == ContentType.M4A
    # AudioFormat normalizes a raw bps bandwidth to kbps.
    assert details.audio_format.bit_rate == 203
    assert client.playurl_calls == [("BV1fixture", None, 1001)]


async def test_aac64_quality_selects_30216() -> None:
    """The aac64 ceiling selects the 64k AAC candidate."""
    data = load_playurl()
    provider, _ = await make_provider(data, overrides(**{constants.CONF_QUALITY: "aac64"}))

    details = await provider.get_stream_details("BV1fixture#1001", MediaType.TRACK)

    assert details.path == audio_url(data, 30216)
    assert details.audio_format.content_type == ContentType.M4A


async def test_auto_selects_flac_when_candidate_is_present() -> None:
    """auto prefers the added FLAC candidate (30251) over every AAC stream."""
    data = add_flac(load_playurl())
    provider, _ = await make_provider(data, overrides())

    details = await provider.get_stream_details("BV1fixture#1001", MediaType.TRACK)

    assert details.path == FLAC_URL
    assert details.audio_format.content_type == ContentType.FLAC
    assert details.audio_format.bit_rate == 1411


async def test_aac192_ceiling_never_selects_flac() -> None:
    """quality=aac192 keeps the FLAC candidate out even when it is available."""
    data = add_flac(load_playurl())
    provider, _ = await make_provider(data, overrides(**{constants.CONF_QUALITY: "aac192"}))

    details = await provider.get_stream_details("BV1fixture#1001", MediaType.TRACK)

    assert details.path == audio_url(data, 30280)
    assert details.path != FLAC_URL
    assert details.audio_format.content_type == ContentType.M4A


# ---------------------------------------------------------------------------
# StreamDetails contract
# ---------------------------------------------------------------------------
async def test_stream_details_contract() -> None:
    """StreamDetails carries HTTP headers, seeking and a clamped lifetime."""
    data = load_playurl()
    provider, _ = await make_provider(data, overrides())

    details = await provider.get_stream_details("BV1fixture#1001", MediaType.TRACK)

    assert details.provider == PROVIDER
    assert details.item_id == "BV1fixture#1001"
    assert details.stream_type == StreamType.HTTP
    assert details.duration == 213
    assert details.can_seek is True
    assert details.allow_seek is True

    # ffmpeg needs the Bilibili referer/UA for the CDN to serve the audio.
    assert details.extra_input_args == constants.FFMPEG_HEADERS
    headers = "\n".join(details.extra_input_args)
    assert "Referer:" in headers
    assert "User-Agent:" in headers

    # The fixture deadline is far in the future, so this is clamped to 7200.
    assert 300 <= details.expiration <= 7200
    assert details.expiration == constants.STREAM_EXPIRATION_MAX
