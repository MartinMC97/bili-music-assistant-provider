"""Unit tests for :mod:`bilibili.catalog` using a fake client (no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from bilibili import catalog
from bilibili.auth import BilibiliCredential
from music_assistant_models.errors import MediaNotFoundError
from music_assistant_models.media_items import (
    BrowseFolder,
    Playlist,
    RecommendationFolder,
    Track,
    UniqueList,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PROVIDER = "bilibili_test"
LOGIN = BilibiliCredential(sessdata="sess", bili_jct="jct", dedeuserid="42")
ANONYMOUS = BilibiliCredential()


def load_fixture(name: str) -> Any:
    """Load a recorded API response from ``tests/fixtures``."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class FakeClient:
    """Stand-in for :class:`bilibili.client.BilibiliClient` with canned payloads."""

    def __init__(
        self,
        *,
        credential: BilibiliCredential | None = None,
        folders: list[Any] | None = None,
        collected_pages: list[dict[str, Any]] | None = None,
        fav_item_pages: dict[int, dict[str, Any]] | None = None,
        music_rank_items: list[Any] | None = None,
        new_music_items: list[Any] | None = None,
        region_items: list[Any] | None = None,
    ) -> None:
        self.credential = credential if credential is not None else LOGIN
        self.folders = load_fixture("fav_folders")["data"]["list"] if folders is None else folders
        self.collected_pages = collected_pages or [{"list": [], "has_more": False}]
        self.fav_item_pages = fav_item_pages or {1: load_fixture("fav_items")["data"]}
        self.music_rank_items = (
            load_fixture("music_rank")["data"]["list"]
            if music_rank_items is None
            else music_rank_items
        )
        self.new_music_items = (
            load_fixture("new_music")["data"]["list"]
            if new_music_items is None
            else new_music_items
        )
        self.region_items = (
            load_fixture("ranking_region")["data"] if region_items is None else region_items
        )
        self.calls: list[tuple[Any, ...]] = []

    async def fav_folders(self, mid: int) -> list[Any]:
        """Return the canned created folders."""
        self.calls.append(("fav_folders", mid))
        return self.folders

    async def fav_collected(self, mid: int, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        """Return one canned page of collected folders."""
        self.calls.append(("fav_collected", mid, page, page_size))
        if 1 <= page <= len(self.collected_pages):
            return self.collected_pages[page - 1]
        return {"list": [], "has_more": False}

    async def fav_items(self, media_id: int, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        """Return one canned page of a favorite folder."""
        self.calls.append(("fav_items", media_id, page, page_size))
        return self.fav_item_pages.get(page, {"medias": [], "has_more": False})

    async def music_rank(self, ps: int = 30) -> list[Any]:
        """Return the canned music centre ranking."""
        self.calls.append(("music_rank", ps))
        return self.music_rank_items

    async def new_music(self) -> list[Any]:
        """Return the canned new music list."""
        self.calls.append(("new_music",))
        return self.new_music_items

    async def ranking_region(self, rid: int = 3, day: int = 3) -> list[Any]:
        """Return the canned region ranking."""
        self.calls.append(("ranking_region", rid, day))
        return self.region_items


# ---------------------------------------------------------------------------
# favorites
# ---------------------------------------------------------------------------
async def test_iter_favorite_playlists_maps_created_and_collected() -> None:
    """Created and collected folders are mapped and deduplicated by item id."""
    client = FakeClient(
        collected_pages=[
            {
                "list": [
                    {
                        "id": 123456,
                        "title": "重复收藏",
                        "upper": {"mid": 7, "name": "重复用户"},
                    },
                    {
                        "id": 777,
                        "title": "订阅的收藏夹",
                        "media_count": 2,
                        "upper": {"mid": 8, "name": "收藏夹主人"},
                    },
                ],
                "has_more": False,
            }
        ]
    )

    playlists = [item async for item in catalog.iter_favorite_playlists(client, PROVIDER)]

    assert [item.item_id for item in playlists] == [
        "fav:123456",
        "fav:654321",
        "fav:777",
    ]
    assert [item.name for item in playlists] == ["我喜欢的音乐", "夜跑", "订阅的收藏夹"]
    assert all(isinstance(item, Playlist) for item in playlists)
    assert all(item.provider == PROVIDER for item in playlists)
    assert all(item.is_editable is False for item in playlists)
    assert playlists[2].owner == "收藏夹主人"
    assert client.calls == [
        ("fav_folders", 42),
        ("fav_collected", 42, 1, 20),
    ]


async def test_iter_favorite_playlists_paginates_collected_folders() -> None:
    """Collected folders continue while the API reports has_more."""
    client = FakeClient(
        folders=[],
        collected_pages=[
            {
                "list": [{"id": 1, "title": "第一页", "upper": {"name": "A"}}],
                "has_more": True,
            },
            {
                "list": [{"id": 2, "title": "第二页", "upper": {"name": "B"}}],
                "has_more": False,
            },
        ],
    )

    playlists = [item async for item in catalog.iter_favorite_playlists(client, PROVIDER)]

    assert [item.item_id for item in playlists] == ["fav:1", "fav:2"]
    assert client.calls == [
        ("fav_folders", 42),
        ("fav_collected", 42, 1, 20),
        ("fav_collected", 42, 2, 20),
    ]


async def test_iter_favorite_playlists_skips_malformed_folders() -> None:
    """A broken folder entry is skipped without aborting the listing."""
    client = FakeClient(
        folders=[None, {}, {"title": "缺少 id"}, {"id": 9, "title": "有效夹"}],
        collected_pages=[
            {
                "list": [None, {"id": 10, "title": "订阅夹"}],
                "has_more": False,
            }
        ],
    )

    playlists = [item async for item in catalog.iter_favorite_playlists(client, PROVIDER)]

    assert [item.item_id for item in playlists] == ["fav:9", "fav:10"]


async def test_anonymous_credential_yields_no_favorites() -> None:
    """Anonymous users get an empty list and no API calls at all."""
    client = FakeClient(credential=ANONYMOUS)

    playlists = [item async for item in catalog.iter_favorite_playlists(client, PROVIDER)]

    assert playlists == []
    assert client.calls == []


async def test_get_favorite_playlist() -> None:
    """A folder id resolves to the matching Playlist and errors when missing."""
    client = FakeClient()

    playlist = await catalog.get_favorite_playlist(client, PROVIDER, "fav:654321")

    assert playlist.item_id == "fav:654321"
    assert playlist.name == "夜跑"
    assert playlist.provider == PROVIDER

    with pytest.raises(MediaNotFoundError):
        await catalog.get_favorite_playlist(client, PROVIDER, "fav:999999")


@pytest.mark.parametrize(
    "bad_id",
    ["", "12345", "fav:", "fav:abc", "fav:-1", "fav:1:2", None],
)
async def test_invalid_playlist_ids_raise_not_found(bad_id: Any) -> None:
    """Every malformed ``fav:`` id raises MediaNotFoundError, without a request."""
    client = FakeClient()

    with pytest.raises(MediaNotFoundError):
        await catalog.get_favorite_tracks(client, PROVIDER, bad_id)
    with pytest.raises(MediaNotFoundError):
        await catalog.get_favorite_playlist(client, PROVIDER, bad_id)

    assert client.calls == []


async def test_get_favorite_tracks_skips_invalid_items_and_numbers_positions() -> None:
    """Unavailable items are skipped and the remaining positions are contiguous."""
    client = FakeClient()

    tracks = await catalog.get_favorite_tracks(client, PROVIDER, "fav:123456")

    assert [track.item_id for track in tracks] == ["BV1GJ411x7h7", "BV1d4411N7zD"]
    assert all(isinstance(track, Track) for track in tracks)
    assert [track.position for track in tracks] == [1, 2]
    assert client.calls == [("fav_items", 123456, 1, 20)]


async def test_get_favorite_tracks_page_offset() -> None:
    """Page N starts at ``N * 20 + 1`` and requests page N + 1 from the API."""
    medias = [
        {"bvid": "BV1page", "title": "第一首", "duration": 10, "upper": {"name": "A"}},
        {"bvid": "BV2dead", "title": "失效", "attr": 1},
        {"bvid": "BV3page", "title": "第二首", "duration": 20, "upper": {"name": "A"}},
    ]
    client = FakeClient(
        fav_item_pages={2: {"medias": medias, "has_more": True}},
    )

    tracks = await catalog.get_favorite_tracks(client, PROVIDER, "fav:123456", page=1)

    assert [track.item_id for track in tracks] == ["BV1page", "BV3page"]
    assert [track.position for track in tracks] == [21, 22]
    assert client.calls == [("fav_items", 123456, 2, 20)]


async def test_get_favorite_tracks_skips_parser_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A parser exception on one entry does not abort the page."""
    original = catalog.parse_fav_item

    def flaky_parser(item: Any, provider_instance: str) -> Track | None:
        if isinstance(item, dict) and item.get("bvid") == "BOOM":
            raise RuntimeError("boom")
        return original(item, provider_instance)

    monkeypatch.setattr(catalog, "parse_fav_item", flaky_parser)
    medias = [
        {"bvid": "BVok", "title": "正常", "duration": 10, "upper": {"name": "A"}},
        {"bvid": "BOOM", "title": "解析失败"},
        None,
        {},
    ]
    client = FakeClient(
        fav_item_pages={1: {"medias": medias, "has_more": False}},
    )

    tracks = await catalog.get_favorite_tracks(client, PROVIDER, "fav:123456")

    assert [track.item_id for track in tracks] == ["BVok"]
    assert [track.position for track in tracks] == [1]


# ---------------------------------------------------------------------------
# recommendations
# ---------------------------------------------------------------------------
def test_recommendation_rows() -> None:
    """The three home rows have stable ids, provider, name and icon."""
    rows = catalog.recommendation_rows(PROVIDER)

    assert [row.item_id for row in rows] == [
        "rank_music",
        "new_music",
        "region_music",
    ]
    assert all(isinstance(row, RecommendationFolder) for row in rows)
    assert all(row.provider == PROVIDER for row in rows)
    assert all(row.name for row in rows)
    assert all(row.icon for row in rows)
    assert rows[0].name == "音乐综合榜"
    assert rows[1].name == "新歌"
    assert rows[2].name == "分区榜"


async def test_recommendation_items_rank_music() -> None:
    """The rank row uses the music centre ranking and the corresponding parser."""
    client = FakeClient()

    items = await catalog.recommendation_items(client, PROVIDER, "rank_music")

    assert isinstance(items, UniqueList)
    assert len(items) == 5
    assert all(isinstance(item, Track) for item in items)
    assert items[0].item_id == "BV1HV411m7DE"
    assert items[0].name.startswith("Lieder Ohne Worte")
    assert client.calls == [("music_rank", 30)]


async def test_recommendation_items_new_music() -> None:
    """The new-music row uses the new music endpoint and parser."""
    client = FakeClient()

    items = await catalog.recommendation_items(client, PROVIDER, "new_music")

    assert len(items) == 5
    assert items[0].name == "AIYOU"
    assert items[0].artists[0].name == "Eve"
    assert client.calls == [("new_music",)]


async def test_recommendation_items_region_music() -> None:
    """The region row requests rid=3 and uses the ranking parser."""
    client = FakeClient()

    items = await catalog.recommendation_items(client, PROVIDER, "region_music")

    assert len(items) == 5
    assert items[0].item_id == "BV17VZpY1ENA"
    assert items[0].duration == 15
    assert items[2].duration == 3619
    assert client.calls == [("ranking_region", 3, 3)]


async def test_recommendation_items_skip_failures_and_duplicates() -> None:
    """Malformed and duplicate entries are dropped, keeping valid tracks."""
    item = load_fixture("music_rank")["data"]["list"][0]
    client = FakeClient(music_rank_items=[item, item, None, {}, "not-a-dict"])

    items = await catalog.recommendation_items(client, PROVIDER, "rank_music")

    assert len(items) == 1
    assert items[0].item_id == "BV1HV411m7DE"


async def test_recommendation_items_unknown_row() -> None:
    """An unknown row id raises MediaNotFoundError without calling the API."""
    client = FakeClient()

    with pytest.raises(MediaNotFoundError):
        await catalog.recommendation_items(client, PROVIDER, "does_not_exist")

    assert client.calls == []


# ---------------------------------------------------------------------------
# browse
# ---------------------------------------------------------------------------
async def test_browse_root_returns_three_folders() -> None:
    """Both root spellings return the rank/new/favorites browse folders."""
    for path in ("", "bilibili://", "  bilibili://  "):
        client = FakeClient()

        items = await catalog.browse_items(client, PROVIDER, path)

        assert len(items) == 3
        assert all(isinstance(item, BrowseFolder) for item in items)
        assert [item.item_id for item in items] == ["rank", "new", "favorites"]
        assert [item.path for item in items] == [
            f"{PROVIDER}://rank",
            f"{PROVIDER}://new",
            f"{PROVIDER}://favorites",
        ]
        assert [item.name for item in items] == ["排行榜", "新歌", "收藏夹"]
        assert all(item.provider == PROVIDER for item in items)
        assert client.calls == []


async def test_browse_rank_and_new() -> None:
    """The rank/new children return tracks and accept a full uri path."""
    client = FakeClient()

    rank_items = await catalog.browse_items(client, PROVIDER, "bilibili://rank")
    new_items = await catalog.browse_items(client, PROVIDER, "new/")

    assert len(rank_items) == 5
    assert all(isinstance(item, Track) for item in rank_items)
    assert rank_items[0].item_id == "BV1HV411m7DE"
    assert len(new_items) == 5
    assert new_items[0].name == "AIYOU"
    assert [call[0] for call in client.calls] == ["music_rank", "new_music"]


async def test_browse_favorites() -> None:
    """The favorites child returns the same playlists as the library listing."""
    client = FakeClient()

    items = await catalog.browse_items(client, PROVIDER, "favorites")

    assert [item.item_id for item in items] == ["fav:123456", "fav:654321"]
    assert all(isinstance(item, Playlist) for item in items)


async def test_browse_favorites_anonymous() -> None:
    """Anonymous users see an empty favorites folder, not an error."""
    client = FakeClient(credential=ANONYMOUS)

    items = await catalog.browse_items(client, PROVIDER, "favorites")

    assert items == []
    assert client.calls == []


@pytest.mark.parametrize("path", ["artist", "rank/extra", "bilibili://unknown"])
async def test_browse_unknown_path(path: str) -> None:
    """Unknown browse paths raise MediaNotFoundError."""
    client = FakeClient()

    with pytest.raises(MediaNotFoundError):
        await catalog.browse_items(client, PROVIDER, path)

    assert client.calls == []


async def test_iter_favorite_tracks_skips_all_invalid_page() -> None:
    """An all-invalid page must not end the folder; positions stay continuous."""
    valid = load_fixture("fav_items")["data"]["medias"][0]
    invalid = {**valid, "attr": 9, "bvid": "BV1invalid"}
    client = FakeClient(
        fav_item_pages={
            1: {"medias": [invalid, invalid], "has_more": True},
            2: {"medias": [valid], "has_more": False},
        }
    )

    tracks = [
        track
        async for track in catalog.iter_favorite_tracks(
            client, PROVIDER, "fav:123456", max_tracks=100
        )
    ]

    assert [track.item_id for track in tracks] == [valid["bvid"]]
    assert tracks[0].position == 1


async def test_iter_favorite_tracks_positions_are_global() -> None:
    """Positions continue across page boundaries instead of restarting per page."""
    valid = load_fixture("fav_items")["data"]["medias"][0]
    other = {**valid, "bvid": "BV1other", "title": "Other"}
    client = FakeClient(
        fav_item_pages={
            1: {"medias": [valid], "has_more": True},
            2: {"medias": [other], "has_more": False},
        }
    )

    tracks = [
        track
        async for track in catalog.iter_favorite_tracks(
            client, PROVIDER, "fav:123456", max_tracks=100
        )
    ]

    assert [track.position for track in tracks] == [1, 2]
