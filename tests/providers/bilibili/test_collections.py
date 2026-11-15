"""
Unit tests for Bilibili UP collections (合集) as Music Assistant playlists.

Fully offline: a fake client serves the recorded ``tests/fixtures`` payloads.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from music_assistant_models.config_entries import ProviderConfig
from music_assistant_models.enums import (
    MediaType,
    ProviderFeature,
    ProviderStage,
    ProviderType,
)
from music_assistant_models.errors import MediaNotFoundError

from bilibili import BilibiliProvider, collections, constants, parsers
from bilibili.auth import BilibiliCredential
from bilibili.client import BilibiliClient

from .fakes import FakeMassBase

PROVIDER = "bilibili_collections_test"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
MANIFEST = SimpleNamespace(
    type=ProviderType.MUSIC,
    domain="bilibili",
    name="Bilibili",
    stage=ProviderStage.ALPHA,
)
MID = "3546851714271831"
SID = "7817533"


# ---------------------------------------------------------------------------
# fixtures / fake collaborators
# ---------------------------------------------------------------------------
def load_fixture(name: str) -> dict[str, Any]:
    """Return a fresh copy of a recorded API payload."""
    return copy.deepcopy(json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8")))


def season_data() -> dict[str, Any]:
    """Return the recorded season archives ``data`` object."""
    return cast("dict[str, Any]", load_fixture("season_archives")["data"])


def up_collections_data() -> dict[str, Any]:
    """Return the recorded UP collections ``data`` object."""
    return cast("dict[str, Any]", load_fixture("space_collections")["data"])


def view_with_collection() -> dict[str, Any]:
    """Return a minimal view payload whose video belongs to a collection."""
    return {
        "bvid": "BV15aWu61Emu",
        "aid": 115123679206788,
        "title": "45分钟抖音热歌音乐合集（纯享版）",  # noqa: RUF001 - Chinese test data
        "pic": "https://i0.hdslb.com/bfs/archive/fixture-view.jpg",
        "pubdate": 1794740400,
        "duration": 2650,
        "pages": [{"cid": 99112233, "page": 1, "part": "正片", "duration": 2650}],
        "owner": {"mid": 1696279451, "name": "Bobaya6"},
        "stat": {"view": 12345},
        "ugc_season": {
            "id": 6362662,
            "title": "45分钟音乐合集（听完下课）",  # noqa: RUF001 - Chinese test data
            "cover": "https://archive.biliimg.com/bfs/archive/fixture-view-season.jpg",
            "mid": 1696279451,
            "intro": "合集简介",
        },
    }


class NoNetworkSession:
    """Fail loudly if a test reaches the network."""

    def get(self, *args: Any, **kwargs: Any) -> Any:
        """Reject every request."""
        raise AssertionError("collection tests must not perform HTTP requests")


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


class FakeMass(FakeMassBase):
    """Minimal ``MusicAssistant`` stand-in with network access disabled."""

    def __init__(self) -> None:
        """Build the stand-in."""
        super().__init__()
        self.http_session = NoNetworkSession()
        self.config = FakeMassConfig()


class FakeClient:
    """Fake Bilibili client serving canned collection payloads."""

    def __init__(self) -> None:
        """Initialise the canned payloads and call log."""
        self.credential = BilibiliCredential()
        self.calls: list[tuple[Any, ...]] = []
        self.search_items: list[dict[str, Any]] = []
        self.view_data: dict[str, Any] = {}
        self.season_payloads: dict[int, dict[str, Any]] = {
            int(SID): season_data(),
            6362662: {
                "meta": {
                    "season_id": 6362662,
                    "mid": 1696279451,
                    "title": "45分钟音乐合集（听完下课）",  # noqa: RUF001 - Chinese test data
                    "cover": "https://archive.biliimg.com/bfs/archive/fixture-view-season.jpg",
                    "total": 1,
                },
                "page": {"page_num": 1, "page_size": 100, "total": 1},
                "archives": [],
            },
        }
        self.collections_payload = up_collections_data()
        self.card_names: dict[int, str] = {
            3546851714271831: "LofiRainyNight",
            1696279451: "Bobaya6",
        }

    async def search_video(
        self, keyword: str, page: int = 1, page_size: int = 20
    ) -> list[dict[str, Any]]:
        """Record the call and return canned music results."""
        self.calls.append(("search_video", keyword, page, page_size))
        return list(self.search_items)

    async def view(self, bvid: str | None = None, aid: int | None = None) -> dict[str, Any]:
        """Return the canned view payload."""
        self.calls.append(("view", bvid if bvid is not None else aid))
        return self.view_data

    async def season_archives(
        self,
        season_id: int,
        page: int = 1,
        page_size: int = constants.COLLECTION_PAGE_SIZE,
        sort_reverse: bool = False,
    ) -> dict[str, Any]:
        """Return the canned archives payload for one season id."""
        self.calls.append(("season_archives", season_id, page, page_size))
        payload = self.season_payloads.get(int(season_id))
        if payload is None:
            raise MediaNotFoundError(f"no fixture for season {season_id}")
        data = copy.deepcopy(payload)
        archives = data.get("archives") or []
        if int(page) > 1:
            data["archives"] = []
        else:
            data["archives"] = archives
        return data

    async def space_collections(
        self, mid: int, page: int = 1, page_size: int = 20
    ) -> dict[str, Any]:
        """Return the canned UP collections payload."""
        self.calls.append(("space_collections", mid, page, page_size))
        return copy.deepcopy(self.collections_payload)

    async def space_card(self, mid: int) -> dict[str, Any]:
        """Return a canned UP card payload."""
        self.calls.append(("space_card", mid))
        name = self.card_names.get(int(mid), "")
        return {"card": {"mid": mid, "name": name}}


if TYPE_CHECKING:
    from music_assistant.mass import MusicAssistant
    from music_assistant_models.media_items import Playlist, Track
    from music_assistant_models.provider import ProviderManifest


async def make_provider(client: FakeClient) -> BilibiliProvider:
    """Build a provider with an injected fake client."""
    config = ProviderConfig(
        type=ProviderType.MUSIC,
        domain="bilibili",
        instance_id=PROVIDER,
        values={},
    )
    provider = BilibiliProvider(
        cast("MusicAssistant", FakeMass()),
        cast("ProviderManifest", MANIFEST),
        config,
        {ProviderFeature.SEARCH, ProviderFeature.LIBRARY_PLAYLISTS},
    )
    for entry in await provider.get_config_entries():
        config.values[entry.key] = copy.deepcopy(entry)
    provider._client = cast("BilibiliClient", client)
    return provider


# ---------------------------------------------------------------------------
# query parsing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("BSEASON:7817533", "7817533"),
        ("bseason: 7817533", "7817533"),
        ("  BSEASON:7817533  ", "7817533"),
        ("https://space.bilibili.com/3546851714271831/lists/7817533?type=season", "7817533"),
        # the form Music Assistant passes to providers (it replaces "/" with " ")
        ("https:  space.bilibili.com 3546851714271831 lists 7817533?type=season", "7817533"),
        (
            "https://space.bilibili.com/3546851714271831/channel/collectiondetail?sid=7817533",
            "7817533",
        ),
        # series URLs are not supported and must not become a collection
        ("https://space.bilibili.com/3546851714271831/lists/7817533?type=series", None),
        # a bare number is never a collection id
        ("7817533", None),
        ("周杰伦", None),
        ("BV1FMsn6REhn", None),
        ("", None),
    ],
)
def test_season_id_from_query(query: str, expected: str | None) -> None:
    """Only explicit collection queries resolve to a season id."""
    assert collections.season_id_from_query(query) == expected


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("UP:3546851714271831", "3546851714271831"),
        ("up: 3546851714271831", "3546851714271831"),
        ("https://space.bilibili.com/3546851714271831", "3546851714271831"),
        ("https://space.bilibili.com/3546851714271831/video", "3546851714271831"),
        ("BSEASON:7817533", None),
        ("3546851714271831", None),
        ("周杰伦", None),
    ],
)
def test_up_mid_from_query(query: str, expected: str | None) -> None:
    """Only explicit UP queries resolve to a mid."""
    assert collections.up_mid_from_query(query) == expected


@pytest.mark.parametrize(
    ("item_id", "expected"),
    [("season:7817533", "7817533"), ("season:6", "6"), ("fav:123", None), ("7817533", None)],
)
def test_season_id_from_item_id(item_id: str, expected: str | None) -> None:
    """Playlist item ids carry the season id behind the ``season:`` prefix."""
    assert collections.season_id_from_item_id(item_id) == expected


def test_season_id_from_view() -> None:
    """A view payload exposes the collection id through ``ugc_season``."""
    data = view_with_collection()
    assert collections.season_id_from_view(data) == "6362662"
    assert collections.season_id_from_view({}) is None
    assert collections.season_id_from_view({"ugc_season": {}}) is None


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------
def test_parse_collection() -> None:
    """A collection meta block becomes a non-editable Playlist."""
    meta = season_data()["meta"]
    playlist = parsers.parse_collection(meta, PROVIDER, "LofiRainyNight")

    assert playlist is not None
    assert playlist.item_id == "season:7817533"
    assert playlist.provider == PROVIDER
    assert playlist.name == "「Chill Lofi Music」Lofi Rainy Night.🌧️雨夜备忘录（2016）"  # noqa: RUF001
    assert playlist.owner == "LofiRainyNight"
    assert playlist.is_editable is False
    assert playlist.metadata.images
    assert "Lofi music" in (playlist.metadata.description or "")
    mapping = next(iter(playlist.provider_mappings))
    assert mapping.url == "https://space.bilibili.com/3546851714271831/lists/7817533?type=season"


def test_parse_collection_rejects_payload_without_season_id() -> None:
    """Malformed meta payloads are skipped instead of raising."""
    assert parsers.parse_collection({}, PROVIDER) is None
    assert parsers.parse_collection({"season_id": "abc"}, PROVIDER) is None


def test_parse_collection_archive() -> None:
    """An archive entry becomes a Track with position-ready fields."""
    archive = season_data()["archives"][0]
    track = parsers.parse_collection_archive(archive, PROVIDER, "LofiRainyNight", 3546851714271831)

    assert track is not None
    assert track.item_id == "BV1uVa5zXEyH"
    assert track.duration == 2650
    # Editorial collection titles keep their decoration instead of being cleaned.
    assert "Lofi" in track.name
    assert track.artists
    assert track.artists[0].name == "LofiRainyNight"
    assert track.artists[0].item_id == "3546851714271831"


def test_parse_collection_archive_rejects_incomplete_entry() -> None:
    """Entries without a bvid or title are skipped."""
    assert parsers.parse_collection_archive({}, PROVIDER) is None
    assert parsers.parse_collection_archive({"bvid": "BV1"}, PROVIDER) is None


def test_parse_up_card() -> None:
    """A card payload yields ``(name, mid)``."""
    assert parsers.parse_up_card({"card": {"mid": 1, "name": "Lofi"}}) == ("Lofi", 1)
    assert parsers.parse_up_card({"card": {"mid": 1}}) is None
    assert parsers.parse_up_card({}) is None


# ---------------------------------------------------------------------------
# provider wiring
# ---------------------------------------------------------------------------
async def test_search_bseason_returns_playlist_with_owner() -> None:
    """``BSEASON:<id>`` returns one playlist; the owner comes from the card API."""
    client = FakeClient()
    provider = await make_provider(client)

    results = await provider.search("BSEASON:7817533", [MediaType.TRACK, MediaType.PLAYLIST])

    assert results.tracks == []
    assert [item.item_id for item in results.playlists] == ["season:7817533"]
    assert cast("Playlist", results.playlists[0]).owner == "LofiRainyNight"


async def test_search_collection_url() -> None:
    """A pasted collection URL works the same as the explicit prefix."""
    client = FakeClient()
    provider = await make_provider(client)

    url = "https://space.bilibili.com/3546851714271831/lists/7817533?type=season"
    results = await provider.search(url, [MediaType.PLAYLIST])

    assert [item.item_id for item in results.playlists] == ["season:7817533"]


async def test_search_bseason_without_playlist_media_type_does_nothing() -> None:
    """Without the playlist media type the collection API is not queried."""
    client = FakeClient()
    provider = await make_provider(client)

    results = await provider.search("BSEASON:7817533", [MediaType.TRACK])

    assert results.playlists == []
    assert not any(call[0] == "season_archives" for call in client.calls)


async def test_search_unknown_collection_returns_empty() -> None:
    """An unknown season id is an empty result, not a provider error."""
    client = FakeClient()
    provider = await make_provider(client)

    results = await provider.search("BSEASON:999999", [MediaType.PLAYLIST])

    assert results.playlists == []


async def test_search_up_lists_collections() -> None:
    """``UP:<mid>`` returns that UP's collections as playlists."""
    client = FakeClient()
    provider = await make_provider(client)

    results = await provider.search("UP:3546851714271831", [MediaType.PLAYLIST])

    assert [item.item_id for item in results.playlists] == [
        "season:7817533",
        "season:6929179",
    ]
    assert all(cast("Playlist", item).owner == "LofiRainyNight" for item in results.playlists)


async def test_search_bv_includes_its_collection() -> None:
    """Searching a BV video that belongs to a collection returns both."""
    client = FakeClient()
    client.view_data = view_with_collection()
    provider = await make_provider(client)

    results = await provider.search("BV15aWu61Emu", [MediaType.TRACK, MediaType.PLAYLIST])

    assert [track.item_id for track in results.tracks] == ["BV15aWu61Emu"]
    assert [item.item_id for item in results.playlists] == ["season:6362662"]


async def test_search_bare_number_stays_a_music_search() -> None:
    """A numeric query is not a collection id and goes to the music search."""
    client = FakeClient()
    provider = await make_provider(client)

    await provider.search("7817533", [MediaType.TRACK, MediaType.PLAYLIST])

    assert [call[0] for call in client.calls] == ["search_video"]


async def test_get_playlist_routes_collection_ids() -> None:
    """``season:`` ids are resolved by the collection path."""
    client = FakeClient()
    provider = await make_provider(client)

    playlist = await provider.get_playlist("season:7817533")

    assert playlist.item_id == "season:7817533"
    assert playlist.name.startswith("「Chill Lofi Music」")


async def test_get_playlist_tracks_pages_the_collection() -> None:
    """Collection tracks come back paginated with contiguous positions."""
    client = FakeClient()
    provider = await make_provider(client)

    tracks = await provider.get_playlist_tracks("season:7817533", page=0)

    assert [track.item_id for track in tracks] == ["BV1uVa5zXEyH", "BV1pwa3zYENm", "BV1bZaUzSEom"]
    assert [track.position for track in tracks] == [1, 2, 3]
    assert cast("Track", tracks[0]).artists[0].name == "LofiRainyNight"
    # one page of 100 items is requested
    season_calls = [call for call in client.calls if call[0] == "season_archives"]
    assert season_calls[-1][3] == constants.COLLECTION_PAGE_SIZE

    assert await provider.get_playlist_tracks("season:7817533", page=1) == []


async def test_collection_metadata_and_owner_lookups_are_cached() -> None:
    """Repeated reads reuse the cached season meta and UP card."""
    client = FakeClient()
    provider = await make_provider(client)

    await provider.get_playlist_tracks("season:7817533", page=0)
    await provider.get_playlist_tracks("season:7817533", page=0)

    card_calls = [call for call in client.calls if call[0] == "space_card"]
    archive_calls = [call for call in client.calls if call[0] == "season_archives"]
    assert len(card_calls) == 1
    # first read: meta lookup + the archives page; second read: the archives page only
    assert len(archive_calls) == 3


async def test_get_playlist_tracks_reports_missing_collection() -> None:
    """A missing collection surfaces as MediaNotFoundError."""
    client = FakeClient()
    provider = await make_provider(client)

    with pytest.raises(MediaNotFoundError):
        await provider.get_playlist_tracks("season:999999", page=0)
