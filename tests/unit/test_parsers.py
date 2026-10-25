"""Unit tests for :mod:`bilibili.parsers` (no network access)."""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path
from typing import Any

import pytest
from bilibili.parsers import (
    clean_title,
    extract_em_tags,
    parse_duration,
    parse_fav_folder,
    parse_fav_item,
    parse_music_center_item,
    parse_ranking_item,
    parse_search_item,
    parse_view,
    to_https,
)
from music_assistant_models.enums import ImageType, LinkType
from music_assistant_models.media_items import (
    ItemMapping,
    MediaItemLink,
    Playlist,
    Track,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PROVIDER = "bilibili_test"
DOMAIN = "bilibili"


def load_fixture(name: str) -> dict[str, Any]:
    """Load a recorded API response from ``tests/fixtures``."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------
def test_extract_em_tags() -> None:
    """Search highlights and HTML entities are removed."""
    raw = '<em class="keyword">晴天</em> &amp; &#39;周杰伦&#39;'
    assert extract_em_tags(raw) == "晴天 & '周杰伦'"
    assert extract_em_tags("plain") == "plain"
    assert extract_em_tags("") == ""


def test_to_https() -> None:
    """Protocol-relative and plain HTTP URLs become HTTPS."""
    assert to_https("//i0.hdslb.com/a.jpg") == "https://i0.hdslb.com/a.jpg"
    assert to_https("http://i1.hdslb.com/b.jpg") == "https://i1.hdslb.com/b.jpg"
    assert to_https("https://i1.hdslb.com/c.jpg") == "https://i1.hdslb.com/c.jpg"
    assert to_https("") == ""
    assert to_https("  //i1.hdslb.com/d.jpg  ") == "https://i1.hdslb.com/d.jpg"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("4:30", 270),
        ("1:02:03", 3723),
        ("60:19", 3619),
        ("0:15", 15),
        ("213", 213),
        (213, 213),
        (213.9, 213),
        (0, 0),
        ("", 0),
        ("not-a-duration", 0),
        ("1:2:3:4", 0),
        (None, 0),
    ],
)
def test_parse_duration(value: Any, expected: int) -> None:
    """Durations accept ``m:ss``, ``h:mm:ss``, numeric strings and ints."""
    assert parse_duration(value) == expected


def test_clean_title_search_fixtures() -> None:
    """Titles from search results lose tags, labels and noise words."""
    items = load_fixture("search_video")["data"]["result"]
    titles = [clean_title(item["title"]) for item in items]

    assert titles[0] == "《晴天》- 周杰伦 -‘故事的小黄花’"
    assert titles[1] == "周杰伦 - 晴天"
    assert titles[3] == "晴天-周杰伦"
    assert titles[4] == "周杰伦——《晴天》"
    for title in titles:
        assert "<em" not in title
        assert "【" not in title and "】" not in title
        assert "无损音质" not in title
        assert "4K" not in title


def test_clean_title_view_and_favorites() -> None:
    """The same cleaning rules apply to detail and favorite payloads."""
    view = load_fixture("view")["data"]
    assert clean_title(view["title"]) == "Never Gonna Give You Up - Rick Astley"

    medias = load_fixture("fav_items")["data"]["medias"]
    assert clean_title(medias[0]["title"]) == "Never Gonna Give You Up - Rick Astley"
    assert clean_title(medias[2]["title"]) == "周杰伦 - 晴天"


def test_clean_title_edge_cases() -> None:
    """Empty input and titles made only of noise are handled."""
    assert clean_title("") == ""
    assert clean_title("   ") == ""
    assert clean_title("晴天（官方MV）") == "晴天"
    assert clean_title("【官方 MV】晴天") == "晴天"
    assert clean_title("【4K修复】") == ""


# ---------------------------------------------------------------------------
# search / ranking
# ---------------------------------------------------------------------------
def test_parse_search_item() -> None:
    """A search result maps every documented field onto the Track model."""
    item = load_fixture("search_video")["data"]["result"][0]
    track = parse_search_item(item, PROVIDER)

    assert isinstance(track, Track)
    assert track.item_id == "BV1BZbSzZEGT"
    assert track.provider == PROVIDER
    assert track.name == "《晴天》- 周杰伦 -‘故事的小黄花’"
    assert track.duration == 270
    assert track.metadata.popularity == 3322671
    # The raw title is kept for reference, with search highlight markup removed.
    assert track.metadata.description == ("【𝐇𝐢-𝐑𝐞𝐬无损音质】｜《晴天》- 周杰伦 -‘故事的小黄花’")
    assert "<em" not in track.name
    assert track.metadata.release_date is not None
    assert track.metadata.release_date.year == 2025
    assert track.metadata.release_date.tzinfo == UTC

    assert len(track.artists) == 1
    artist = track.artists[0]
    assert isinstance(artist, ItemMapping)
    assert artist.item_id == "9666167"
    assert artist.provider == PROVIDER
    assert artist.name == "VV音乐局"

    assert track.metadata.images is not None
    assert len(track.metadata.images) == 1
    image = track.metadata.images[0]
    assert image.type is ImageType.THUMB
    assert image.path.startswith("https://i0.hdslb.com/")
    assert image.provider == PROVIDER
    assert image.remotely_accessible is True

    expected_link = MediaItemLink(
        type=LinkType.WEBSITE,
        url="https://www.bilibili.com/video/BV1BZbSzZEGT",
    )
    assert track.metadata.links == {expected_link}

    assert len(track.provider_mappings) == 1
    mapping = next(iter(track.provider_mappings))
    assert mapping.item_id == "BV1BZbSzZEGT"
    assert mapping.provider_domain == DOMAIN
    assert mapping.provider_instance == PROVIDER
    assert mapping.available is True
    assert mapping.url == "https://www.bilibili.com/video/BV1BZbSzZEGT"


@pytest.mark.parametrize(
    "item",
    [
        {},
        {"bvid": "BV1xx411c7mD"},
        {"title": "晴天"},
        {"bvid": "", "title": "晴天"},
        None,
    ],
)
def test_parse_search_item_invalid(item: Any) -> None:
    """Incomplete search entries are skipped instead of raising."""
    assert parse_search_item(item, PROVIDER) is None


def test_parse_ranking_item() -> None:
    """Ranking entries use the same mapping as search results."""
    items = load_fixture("ranking_region")["data"]
    tracks = [parse_ranking_item(item, PROVIDER) for item in items]

    assert all(isinstance(track, Track) for track in tracks)
    first = tracks[0]
    assert first.item_id == "BV17VZpY1ENA"
    assert first.duration == 15
    assert first.metadata.popularity == 17868708
    assert first.artists[0].item_id == "477676711"
    assert first.artists[0].name == "田一名sir"
    assert first.metadata.images is not None
    assert first.metadata.images[0].path.startswith("https://i1.hdslb.com/")

    assert parse_ranking_item({"bvid": "BV1xx411c7mD"}, PROVIDER) is None
    assert parse_ranking_item({}, PROVIDER) is None


# ---------------------------------------------------------------------------
# view
# ---------------------------------------------------------------------------
def test_parse_view() -> None:
    """A view payload becomes a full Track."""
    data = load_fixture("view")["data"]
    track = parse_view(data, PROVIDER)

    assert isinstance(track, Track)
    assert track.item_id == "BV1GJ411x7h7"
    assert track.provider == PROVIDER
    assert track.name == "Never Gonna Give You Up - Rick Astley"
    assert track.duration == 213
    assert track.metadata.description == "【官方 MV】Never Gonna Give You Up - Rick Astley"
    assert track.metadata.popularity == 107691045

    assert track.artists[0].item_id == "486906719"
    assert track.artists[0].name == "索尼音乐中国"

    assert track.metadata.images is not None
    assert track.metadata.images[0].path == (
        "https://i1.hdslb.com/bfs/archive/5242750857121e05146d5d5b13a47a2a6dd36e98.jpg"
    )
    assert track.metadata.links == {
        MediaItemLink(
            type=LinkType.WEBSITE,
            url="https://www.bilibili.com/video/BV1GJ411x7h7",
        )
    }

    mapping = next(iter(track.provider_mappings))
    assert mapping.item_id == "BV1GJ411x7h7"
    assert mapping.url == "https://www.bilibili.com/video/BV1GJ411x7h7"


def test_parse_view_multi_part_uses_cid() -> None:
    """Multi-part videos get ``bvid#cid`` and the first part duration."""
    data = load_fixture("view")["data"]
    data["pages"] = [
        {"cid": 111, "page": 1, "part": "Part One", "duration": 100},
        {"cid": 222, "page": 2, "part": "Part Two", "duration": 90},
    ]

    track = parse_view(data, PROVIDER)

    assert track.item_id == "BV1GJ411x7h7#111"
    assert track.duration == 100
    mapping = next(iter(track.provider_mappings))
    assert mapping.item_id == "BV1GJ411x7h7#111"


def test_parse_view_aid_only() -> None:
    """A payload without bvid falls back to an ``av`` id."""
    track = parse_view({"aid": 80433022, "title": "晴天", "duration": 12}, PROVIDER)

    assert track.item_id == "av80433022"
    assert track.duration == 12
    mapping = next(iter(track.provider_mappings))
    assert mapping.url == "https://www.bilibili.com/video/av80433022"


# ---------------------------------------------------------------------------
# favorites
# ---------------------------------------------------------------------------
def test_parse_fav_item() -> None:
    """Favorite media entries map to Tracks; unavailable ones are skipped."""
    medias = load_fixture("fav_items")["data"]["medias"]

    first = parse_fav_item(medias[0], PROVIDER)
    assert isinstance(first, Track)
    assert first.item_id == "BV1GJ411x7h7"
    assert first.name == "Never Gonna Give You Up - Rick Astley"
    assert first.duration == 213
    assert first.artists[0].item_id == "123"
    assert first.artists[0].name == "UP-A"
    assert first.metadata.images is not None
    assert first.metadata.images[0].path == "https://i1.hdslb.com/bfs/archive/x.jpg"
    assert first.date_added is not None
    assert first.date_added.year == 2023

    # attr != 0 means the video is unavailable and must be skipped.
    assert parse_fav_item(medias[1], PROVIDER) is None

    third = parse_fav_item(medias[2], PROVIDER)
    assert third is not None
    assert third.name == "周杰伦 - 晴天"
    assert third.duration == 269
    assert third.artists[0].item_id == "789"


def test_parse_fav_item_invalid() -> None:
    """Missing required fields never raise."""
    assert parse_fav_item({}, PROVIDER) is None
    assert parse_fav_item({"bvid": "BV1xx411c7mD"}, PROVIDER) is None
    assert parse_fav_item({"title": "晴天", "attr": 0}, PROVIDER) is None
    assert parse_fav_item(None, PROVIDER) is None


def test_parse_fav_folder() -> None:
    """Favorite folders become non-editable Playlists with a ``fav:`` id."""
    folders = load_fixture("fav_folders")["data"]["list"]
    playlist = parse_fav_folder(folders[0], PROVIDER, "小明")

    assert isinstance(playlist, Playlist)
    assert playlist.item_id == "fav:123456"
    assert playlist.name == "我喜欢的音乐"
    assert playlist.provider == PROVIDER
    assert playlist.owner == "小明"
    assert playlist.is_editable is False

    assert len(playlist.provider_mappings) == 1
    mapping = next(iter(playlist.provider_mappings))
    assert mapping.item_id == "fav:123456"
    assert mapping.provider_domain == DOMAIN
    assert mapping.provider_instance == PROVIDER
    assert mapping.available is True
    assert mapping.url == "https://www.bilibili.com/medialist/detail/ml123456"

    second = parse_fav_folder(folders[1], PROVIDER, "小明")
    assert second.item_id == "fav:654321"
    assert second.name == "夜跑"


def test_parse_fav_folder_without_id_raises() -> None:
    """A folder payload without an id is a programming error, not a Track."""
    with pytest.raises(ValueError):
        parse_fav_folder({"title": "夜跑"}, PROVIDER, "小明")


# ---------------------------------------------------------------------------
# music center
# ---------------------------------------------------------------------------
def test_parse_music_center_item() -> None:
    """Music-center entries keep music metadata and embed the cid."""
    item = load_fixture("music_rank")["data"]["list"][0]
    track = parse_music_center_item(item, PROVIDER)

    assert isinstance(track, Track)
    assert track.item_id == "BV1HV411m7DE#30437803635"
    assert track.name == "Lieder Ohne Worte Op 67 No 4 la Fileuse(The Spinning Song)"
    assert track.duration == 105
    assert track.metadata.description == item["music_title"]
    assert track.metadata.popularity == 73797
    assert track.metadata.images is not None
    assert track.metadata.images[0].path.startswith("https://i0.hdslb.com/bfs/station_src/")

    # The artist is the music-center artist, the id comes from the archive.
    assert track.artists[0].name == "Emiliano Bucci,Felix Mendelssohn"
    assert track.artists[0].item_id == "377681320"

    assert track.metadata.links == {
        MediaItemLink(
            type=LinkType.WEBSITE,
            url="https://www.bilibili.com/video/BV1HV411m7DE",
        )
    }
    mapping = next(iter(track.provider_mappings))
    assert mapping.item_id == "BV1HV411m7DE#30437803635"


def test_parse_music_center_item_related_archive_fallback() -> None:
    """Without a top-level bvid the related archive identifies the video."""
    item = load_fixture("music_rank")["data"]["list"][1]
    item = {key: value for key, value in item.items() if key != "bvid"}

    track = parse_music_center_item(item, PROVIDER)

    assert track is not None
    assert track.item_id == "BV1Le411S7b1#238114795"
    assert track.name == "MUGEN ROAD"
    assert track.duration == 113
    assert track.metadata.popularity == 13513453
    assert track.metadata.links == {
        MediaItemLink(
            type=LinkType.WEBSITE,
            url="https://www.bilibili.com/video/BV1Le411S7b1",
        )
    }


def test_parse_music_center_item_av_fallback() -> None:
    """With only an aid the item id becomes ``av<aid>#<cid>``."""
    item = {
        "music_title": "晴天",
        "aid": "12345",
        "cid": "999",
        "author": "周杰伦",
        "related_archive": {},
    }

    track = parse_music_center_item(item, PROVIDER)

    assert track is not None
    assert track.item_id == "av12345#999"
    assert track.duration == 0
    assert track.metadata.links == {
        MediaItemLink(
            type=LinkType.WEBSITE,
            url="https://www.bilibili.com/video/av12345",
        )
    }


def test_parse_music_center_item_invalid() -> None:
    """Entries without any video identity or title are skipped."""
    assert parse_music_center_item({}, PROVIDER) is None
    assert parse_music_center_item({"music_title": "晴天"}, PROVIDER) is None
    assert parse_music_center_item({"bvid": "BV1xx411c7mD"}, PROVIDER) is None
    assert parse_music_center_item(None, PROVIDER) is None


def test_parsers_tolerate_bad_list_entries() -> None:
    """All list parsers skip nonsense without letting an exception escape."""
    for parser in (
        parse_search_item,
        parse_fav_item,
        parse_music_center_item,
        parse_ranking_item,
    ):
        assert parser({}, PROVIDER) is None
        assert parser({"bvid": None, "title": None}, PROVIDER) is None
