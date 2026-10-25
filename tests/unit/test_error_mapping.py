"""Error translation tests: Bilibili failures -> Music Assistant exceptions.

Covers the provider's ``BiliApiError`` mapping table, the wiring through a public
provider method, and the client's offline network/HTTP error translation.
Everything runs against fake objects; no request leaves the process.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest
from bilibili import BilibiliProvider
from bilibili.auth import BilibiliCredential
from bilibili.client import BiliApiError, BilibiliClient
from music_assistant_models.config_entries import ProviderConfig
from music_assistant_models.enums import MediaType, ProviderStage, ProviderType
from music_assistant_models.errors import (
    MediaNotFoundError,
    MusicAssistantError,
    ResourceTemporarilyUnavailable,
    UnplayableMediaError,
)

PROVIDER = "bilibili_error_mapping"
MANIFEST = SimpleNamespace(
    type=ProviderType.MUSIC,
    domain="bilibili",
    name="Bilibili",
    stage=ProviderStage.ALPHA,
)

#: The full provider translation table from docs/contracts.md.
API_ERROR_TABLE: list[tuple[int, type[MusicAssistantError]]] = [
    (-352, ResourceTemporarilyUnavailable),
    (-412, ResourceTemporarilyUnavailable),
    (-404, MediaNotFoundError),
    (62002, MediaNotFoundError),
    (62004, MediaNotFoundError),
    (87007, UnplayableMediaError),
    (87008, UnplayableMediaError),
]


# ---------------------------------------------------------------------------
# provider translation table
# ---------------------------------------------------------------------------
def translate(code: int) -> MusicAssistantError:
    """Run the provider's translation for an API error code."""
    return BilibiliProvider._translate_error(BiliApiError(code, "boom", "/x/player/playurl"))


@pytest.mark.parametrize(("code", "expected"), API_ERROR_TABLE, ids=str)
def test_bili_api_error_is_translated(code: int, expected: type[MusicAssistantError]) -> None:
    """Each documented Bilibili code maps to the matching MA exception type."""
    translated = translate(code)

    assert isinstance(translated, expected)
    assert not isinstance(translated, BiliApiError)


@pytest.mark.parametrize(("code", "backoff"), [(-352, 30), (-412, 60)])
def test_rate_limit_codes_carry_documented_backoff(code: int, backoff: int) -> None:
    """Risk-control errors tell MA how long to back off before retrying."""
    translated = translate(code)

    assert isinstance(translated, ResourceTemporarilyUnavailable)
    assert translated.backoff_time == backoff


def test_unknown_api_code_falls_back_to_temporary_error() -> None:
    """An unrecognized code is retried instead of being reported as missing media."""
    translated = translate(-999)

    assert isinstance(translated, ResourceTemporarilyUnavailable)
    assert translated.backoff_time == 5


# ---------------------------------------------------------------------------
# wiring through a public provider method
# ---------------------------------------------------------------------------
class RaisingClient:
    """Fake client whose search call always fails with a configured error."""

    def __init__(self, error: Exception) -> None:
        """Store the error to raise."""
        self.error = error
        self.credential = BilibiliCredential()

    async def search_video(
        self, keyword: str, page: int = 1, page_size: int = 20
    ) -> list[dict[str, Any]]:
        """Raise the configured error."""
        raise self.error


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

    def get_raw_provider_config_value(self, instance_id: str, key: str) -> None:
        """No raw overrides: config entries resolve to their defaults."""
        return None


class FakeMass:
    """Minimal ``MusicAssistant`` stand-in."""

    def __init__(self) -> None:
        """Provide only the config object the provider touches."""
        self.cache = None
        self.http_session = None
        self.config = FakeMassConfig()


async def make_provider(error: Exception) -> BilibiliProvider:
    """Build a provider whose search endpoint raises *error*."""
    config = ProviderConfig(
        type=ProviderType.MUSIC,
        domain="bilibili",
        instance_id=PROVIDER,
        values={},
    )
    provider = BilibiliProvider(FakeMass(), MANIFEST, config)
    for entry in await provider.get_config_entries():
        config.values[entry.key] = copy.deepcopy(entry)
    provider._client = RaisingClient(error)
    return provider


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (-352, ResourceTemporarilyUnavailable),
        (-412, ResourceTemporarilyUnavailable),
        (-404, MediaNotFoundError),
        (62002, MediaNotFoundError),
        (87007, UnplayableMediaError),
    ],
    ids=str,
)
async def test_provider_search_raises_translated_error(
    code: int, expected: type[MusicAssistantError]
) -> None:
    """A failing client call surfaces as the translated MA error from search()."""
    provider = await make_provider(BiliApiError(code, "boom", "/search"))

    with pytest.raises(expected):
        await provider.search("晴天", [MediaType.TRACK], limit=3)


async def test_provider_search_rate_limit_keeps_backoff() -> None:
    """The translated backoff survives the provider search path."""
    provider = await make_provider(BiliApiError(-352, "rate limited", "/search"))

    with pytest.raises(ResourceTemporarilyUnavailable) as info:
        await provider.search("晴天", [MediaType.TRACK], limit=3)
    assert info.value.backoff_time == 30


# ---------------------------------------------------------------------------
# client network / HTTP translation (offline fake session)
# ---------------------------------------------------------------------------
class FakeResponse:
    """Minimal async context manager shaped like an aiohttp response."""

    def __init__(self, payload: Any = None, *, status: int = 200) -> None:
        """Store the canned payload and status."""
        self.payload = payload
        self.status = status
        self.cookies: dict[str, str] = {}
        self.headers: dict[str, str] = {}

    async def __aenter__(self) -> FakeResponse:
        """Enter the context manager."""
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        """Leave the context manager without suppressing exceptions."""
        return False

    async def json(self, *args: Any, **kwargs: Any) -> Any:
        """Return the canned JSON payload."""
        return self.payload


class FakeSession:
    """Queue-backed stand-in for ``mass.http_session``."""

    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        """Store the queued responses/errors."""
        self.responses = list(responses)

    def get(self, url: Any, **kwargs: Any) -> FakeResponse:
        """Return the next response or raise the next error."""
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_client(responses: list[FakeResponse | Exception]) -> BilibiliClient:
    """Build a real client on top of a fake HTTP session."""
    mass = SimpleNamespace(http_session=FakeSession(responses))
    return BilibiliClient(mass, BilibiliCredential())


@pytest.mark.parametrize(
    "error",
    [aiohttp.ClientError("connection reset"), TimeoutError("timed out")],
    ids=["client-error", "timeout"],
)
async def test_network_errors_become_temporarily_unavailable(error: Exception) -> None:
    """Connection errors and timeouts are retried, never reported as not found."""
    client = make_client([error])

    with pytest.raises(ResourceTemporarilyUnavailable) as info:
        await client.nav()
    assert info.value.backoff_time == 5


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_http_server_and_rate_limit_errors_are_temporary(status: int) -> None:
    """5xx responses and HTTP 429 are transient, with the server backoff."""
    client = make_client([FakeResponse(None, status=status)])

    with pytest.raises(ResourceTemporarilyUnavailable) as info:
        await client.nav()
    assert info.value.backoff_time == 30


async def test_http_412_becomes_risk_control_error() -> None:
    """An HTML 412 risk-control page maps to -412 and the longer backoff."""
    client = make_client([FakeResponse(None, status=412)])

    with pytest.raises(BiliApiError) as info:
        await client.nav()
    assert info.value.code == -412

    translated = BilibiliProvider._translate_error(info.value)
    assert isinstance(translated, ResourceTemporarilyUnavailable)
    assert translated.backoff_time == 60
