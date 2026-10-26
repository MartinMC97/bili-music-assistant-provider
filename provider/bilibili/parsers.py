"""Convert Bilibili API payloads into Music Assistant models.

Everything in this module is pure and synchronous: it never performs I/O and
never raises for malformed list entries (those are skipped by returning
``None``).  The provider/catalog layers are responsible for the HTTP calls.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from typing import Any

from music_assistant_models.enums import ImageType, LinkType
from music_assistant_models.media_items import (
    ItemMapping,
    MediaItemImage,
    MediaItemLink,
    MediaItemMetadata,
    Playlist,
    ProviderMapping,
    Track,
    UniqueList,
)

from .constants import NOISE_PATTERNS, PROVIDER_DOMAIN

BILIBILI_WEB = "https://www.bilibili.com"
VIDEO_URL = f"{BILIBILI_WEB}/video/{{video_id}}"
FAV_FOLDER_URL = f"{BILIBILI_WEB}/medialist/detail/ml{{folder_id}}"

_HTML_TAG_RE = re.compile(r"<[^>]*>")
_BRACKET_TAG_RE = re.compile(r"【([^】]*)】")
_EMPTY_PARENS_RE = re.compile(r"[（(]\s*[)）]")
_WHITESPACE_RE = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,，、;；:：!?！？])")

# Characters that only ever act as decoration around a Bilibili title.
_EDGE_JUNK = " \t\r\n-—–_|｜/\\·•,，、;；:："

_NOISE_RE = re.compile(
    "|".join(f"(?:{pattern})" for pattern in NOISE_PATTERNS),
    re.IGNORECASE,
)

# Keywords that mark a 【...】 label as noise rather than as part of the song
# title.  NOISE_PATTERNS is reused, plus a few quality/format labels that are
# not covered by it (e.g. plain "官方" or a bare "MV").
_BRACKET_NOISE_RE = re.compile(
    "|".join(
        (
            *NOISE_PATTERNS,
            r"官方",
            r"\bmv\b",
            r"\bpv\b",
            r"hi-?res",
            r"\d+\s*[kp]\b",
            r"无损",
            r"音质",
            r"高清",
            r"修复",
            r"纯享",
            r"完整",
        )
    ),
    re.IGNORECASE,
)

# A few leftovers that NOISE_PATTERNS intentionally leaves alone.
_EXTRA_NOISE_RE = re.compile(
    "|".join(
        (
            r"(?<![A-Za-z0-9])(?:4k|8k|2160p|1440p|1080p|720p|480p)(?![A-Za-z0-9])",
            r"(?:高清|超清|修复|重制|完整|正式)\s*版",
            r"(?<=[\u4e00-\u9fff])\s*(?:mv|pv)(?![A-Za-z])",
            r"\s+(?:mv|pv)\s*$",
        )
    ),
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------
def extract_em_tags(raw: str) -> str:
    """Remove HTML tags (e.g. search ``<em>`` markers) and decode entities."""
    if not raw:
        return ""
    return _HTML_TAG_RE.sub("", html.unescape(str(raw))).strip()


def clean_title(raw: str) -> str:
    """Return a human-friendly track title.

    Strips search highlight tags, HTML entities, decorative ``【...】`` labels,
    the noise words from :data:`constants.NOISE_PATTERNS` and redundant
    whitespace.  The original title is kept by the parsers in
    ``metadata.description``.
    """
    text = extract_em_tags(raw)
    if not text:
        return ""

    # Keep the text of meaningful labels ("【八方来财】"), drop noise labels.
    text = _BRACKET_TAG_RE.sub(
        lambda match: " " if _BRACKET_NOISE_RE.search(match.group(1)) else match.group(1),
        text,
    )
    text = _NOISE_RE.sub(" ", text)
    text = _EXTRA_NOISE_RE.sub(" ", text)
    text = _EMPTY_PARENS_RE.sub(" ", text)
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)

    # Collapse whitespace and strip decoration that ended up at the edges
    # after removing labels/prefixes.
    text = _WHITESPACE_RE.sub(" ", text).strip(_EDGE_JUNK).strip()
    return _WHITESPACE_RE.sub(" ", text).strip()


def parse_duration(value: str | int) -> int:
    """Parse a Bilibili duration (``"4:30"``, ``"1:02:03"`` or int) to seconds."""
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(int(value), 0)
    if not isinstance(value, str):
        return 0

    text = value.strip()
    if not text:
        return 0

    parts = text.split(":")
    if len(parts) > 3:
        return 0

    total = 0
    for part in parts:
        part = part.strip()
        if not part:
            return 0
        try:
            seconds = int(part)
        except ValueError:
            try:
                seconds = int(float(part))
            except ValueError, OverflowError:
                return 0
        if seconds < 0:
            return 0
        total = total * 60 + seconds
    return total


def to_https(url: str) -> str:
    """Normalize a Bilibili asset URL to HTTPS (``//`` and ``http://``)."""
    if not url or not isinstance(url, str):
        return ""
    url = url.strip()
    if not url:
        return ""
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("http://"):
        return f"https://{url[len('http://') :]}"
    return url


# ---------------------------------------------------------------------------
# internal conversion helpers
# ---------------------------------------------------------------------------
def _text(value: Any) -> str:
    """Return a stripped string for an API scalar value."""
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value).strip()


def _cid_value(value: Any) -> str:
    """Return a cid, treating ``0``/``"0"`` (music-center placeholders) as absent."""
    text = _text(value)
    return "" if text in ("", "0") else text


def _as_int(value: Any) -> int | None:
    """Best-effort int conversion; returns None when not numeric."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except TypeError, ValueError:
        try:
            return int(float(value))
        except TypeError, ValueError, OverflowError:
            return None


def _from_timestamp(value: Any) -> datetime | None:
    """Convert a unix timestamp to an aware UTC datetime."""
    seconds = _as_int(value)
    if not seconds or seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except OverflowError, OSError, ValueError:
        return None


def _video_url(bvid: str, aid: Any = None) -> str | None:
    """Build the public video URL, preferring bvid over aid."""
    if bvid:
        return VIDEO_URL.format(video_id=bvid)
    aid_text = _text(aid)
    if aid_text:
        return VIDEO_URL.format(video_id=f"av{aid_text}")
    return None


def _image(cover: Any, provider_instance: str) -> MediaItemImage | None:
    """Build the thumbnail image, or None when the API has no cover."""
    path = to_https(_text(cover))
    if not path:
        return None
    return MediaItemImage(
        type=ImageType.THUMB,
        path=path,
        provider=provider_instance,
        remotely_accessible=True,
    )


def _artist(name: Any, mid: Any, provider_instance: str) -> ItemMapping | None:
    """Build the UP-owner mapping used as the track artist."""
    artist_name = _text(name)
    if not artist_name:
        return None
    return ItemMapping(
        item_id=_text(mid) or artist_name,
        provider=provider_instance,
        name=artist_name,
    )


def _build_track(
    *,
    item_id: str,
    provider_instance: str,
    raw_title: str,
    duration: Any,
    author: Any,
    mid: Any,
    cover: Any,
    popularity: Any,
    bvid: str,
    aid: Any = None,
    pubdate: Any = None,
    date_added: Any = None,
) -> Track:
    """Assemble a Track from already-normalized fields."""
    url = _video_url(bvid, aid)
    image = _image(cover, provider_instance)
    artist = _artist(author, mid, provider_instance)
    # The contract keeps the untouched API title for reference/lyrics matching.
    description = extract_em_tags(raw_title) or None

    metadata = MediaItemMetadata(
        description=description,
        images=UniqueList([image]) if image is not None else None,
        links={MediaItemLink(type=LinkType.WEBSITE, url=url)} if url else None,
        popularity=_as_int(popularity),
        release_date=_from_timestamp(pubdate),
    )

    return Track(
        item_id=item_id,
        provider=provider_instance,
        name=clean_title(raw_title) or description or item_id,
        duration=parse_duration(duration),
        artists=UniqueList([artist]) if artist is not None else UniqueList(),
        metadata=metadata,
        provider_mappings={
            ProviderMapping(
                item_id=item_id,
                provider_domain=PROVIDER_DOMAIN,
                provider_instance=provider_instance,
                available=True,
                url=url,
            )
        },
        date_added=_from_timestamp(date_added),
    )


def parse_page_track(
    data: dict[str, Any],
    page: dict[str, Any],
    base: Track,
    provider_instance: str,
) -> Track | None:
    """Convert one ``pages`` entry of a multi-part video into a Track.

    ``base`` supplies the fields shared by all parts (artist, cover, popularity);
    the page supplies its own cid, title and duration.
    """
    cid = _cid_value(page.get("cid"))
    bvid = _text(data.get("bvid"))
    aid = data.get("aid")
    if not cid or not (bvid or aid):
        return None

    item_id = f"{bvid}#{cid}" if bvid else f"av{_text(aid)}#{cid}"
    page_number = _as_int(page.get("page")) or 0
    part = _text(page.get("part"))
    raw_title = part or (f"{base.name} P{page_number}" if page_number else base.name)
    artist = base.artists[0] if base.artists else None
    image = base.metadata.images[0] if base.metadata.images else None

    track = _build_track(
        item_id=item_id,
        provider_instance=provider_instance,
        raw_title=raw_title,
        duration=page.get("duration") or base.duration,
        author=getattr(artist, "name", None),
        mid=getattr(artist, "item_id", None),
        cover=image.path if image is not None else None,
        popularity=base.metadata.popularity,
        bvid=bvid,
        aid=aid,
        pubdate=data.get("pubdate"),
    )
    if part:
        # Page titles are already human-facing; keep 【Disc1】 labels intact
        # instead of running the aggressive search-result cleaner over them.
        track.name = part
    if page_number:
        track.track_number = page_number
    if base.date_added is not None:
        track.date_added = base.date_added
    return track


def _parse_video_item(item: dict[str, Any], provider_instance: str) -> Track | None:
    """Shared parser for search results and ranking entries."""
    bvid = _text(item.get("bvid"))
    raw_title = _text(item.get("title"))
    if not bvid or not raw_title:
        return None
    return _build_track(
        item_id=bvid,
        provider_instance=provider_instance,
        raw_title=raw_title,
        duration=item.get("duration"),
        author=item.get("author"),
        mid=item.get("mid"),
        cover=item.get("pic"),
        popularity=item.get("play"),
        bvid=bvid,
        aid=item.get("aid"),
        pubdate=item.get("pubdate"),
    )


# ---------------------------------------------------------------------------
# public parsers
# ---------------------------------------------------------------------------
def parse_search_item(item: dict[str, Any], provider_instance: str) -> Track | None:
    """Convert one ``/wbi/search/type`` video entry; invalid entries -> None."""
    if not isinstance(item, dict):
        return None
    return _parse_video_item(item, provider_instance)


def parse_view(data: dict[str, Any], provider_instance: str) -> Track:
    """Convert a ``/x/web-interface/view`` payload into a Track."""
    if not isinstance(data, dict):
        data = {}

    bvid = _text(data.get("bvid"))
    aid = data.get("aid")
    pages = [page for page in (data.get("pages") or []) if isinstance(page, dict)]
    first_page = pages[0] if pages else {}

    cid = _cid_value(first_page.get("cid") or data.get("cid"))
    multi_part = len(pages) > 1

    if bvid:
        item_id = f"{bvid}#{cid}" if multi_part and cid else bvid
    elif aid:
        item_id = f"av{_text(aid)}#{cid}" if multi_part and cid else f"av{_text(aid)}"
    else:
        raise ValueError("view payload has neither bvid nor aid")

    raw_title = _text(data.get("title")) or _text(first_page.get("part")) or item_id
    if multi_part:
        duration = first_page.get("duration") or data.get("duration")
    else:
        duration = data.get("duration") or first_page.get("duration")

    owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
    stat = data.get("stat") if isinstance(data.get("stat"), dict) else {}

    return _build_track(
        item_id=item_id,
        provider_instance=provider_instance,
        raw_title=raw_title,
        duration=duration,
        author=owner.get("name"),
        mid=owner.get("mid"),
        cover=data.get("pic"),
        popularity=stat.get("view"),
        bvid=bvid,
        aid=aid,
        pubdate=data.get("pubdate"),
    )


def parse_fav_item(item: dict[str, Any], provider_instance: str) -> Track | None:
    """Convert one favorite media entry; unavailable items (attr != 0) -> None."""
    if not isinstance(item, dict):
        return None

    attr = _as_int(item.get("attr"))
    if attr not in (None, 0):
        return None

    bvid = _text(item.get("bvid"))
    raw_title = _text(item.get("title"))
    if not bvid or not raw_title:
        return None

    upper = item.get("upper") if isinstance(item.get("upper"), dict) else {}
    return _build_track(
        item_id=bvid,
        provider_instance=provider_instance,
        raw_title=raw_title,
        duration=item.get("duration"),
        author=upper.get("name") or item.get("author"),
        mid=upper.get("mid") or item.get("mid"),
        cover=item.get("cover") or item.get("pic"),
        popularity=item.get("play"),
        bvid=bvid,
        aid=item.get("aid"),
        pubdate=item.get("pubdate"),
        date_added=item.get("fav_time"),
    )


def parse_fav_folder(
    item: dict[str, Any],
    provider_instance: str,
    owner_name: str,
) -> Playlist:
    """Convert one favorite folder entry into a Playlist."""
    if not isinstance(item, dict):
        raise ValueError("favorite folder payload must be a dict")

    folder_id = _text(item.get("id"))
    if not folder_id:
        raise ValueError("favorite folder payload has no id")

    raw_title = _text(item.get("title")) or _text(item.get("name")) or f"收藏夹 {folder_id}"
    item_id = f"fav:{folder_id}"
    url = FAV_FOLDER_URL.format(folder_id=folder_id)

    return Playlist(
        item_id=item_id,
        provider=provider_instance,
        name=clean_title(raw_title) or extract_em_tags(raw_title) or raw_title,
        provider_mappings={
            ProviderMapping(
                item_id=item_id,
                provider_domain=PROVIDER_DOMAIN,
                provider_instance=provider_instance,
                available=True,
                url=url,
            )
        },
        owner=str(owner_name or ""),
        is_editable=False,
    )


def parse_music_center_item(item: dict[str, Any], provider_instance: str) -> Track | None:
    """Convert a music-center entry, falling back to its ``related_archive``.

    The music-centre ``cid`` is a music metadata id, not the video page cid that
    ``/x/player/playurl`` expects. Keep only the video id and let
    :meth:`BilibiliProvider.get_stream_details` resolve the real cid from
    ``/x/web-interface/view``.
    """
    if not isinstance(item, dict):
        return None

    archive = item.get("related_archive") if isinstance(item.get("related_archive"), dict) else {}

    bvid = _text(item.get("bvid")) or _text(archive.get("bvid"))
    aid = _text(item.get("aid")) or _text(archive.get("aid"))

    raw_title = (
        _text(item.get("music_title")) or _text(item.get("title")) or _text(archive.get("title"))
    )
    if not raw_title or not (bvid or aid):
        return None

    item_id = bvid if bvid else f"av{aid}"

    author = item.get("author") or archive.get("username")
    mid = item.get("mid") or archive.get("uid")
    cover = item.get("cover") or archive.get("cover")
    duration = item.get("duration") or archive.get("duration")
    # ``score`` is the music-center play counter; fall back to archive views.
    popularity = item.get("score") or archive.get("vv_count")

    return _build_track(
        item_id=item_id,
        provider_instance=provider_instance,
        raw_title=raw_title,
        duration=duration,
        author=author,
        mid=mid,
        cover=cover,
        popularity=popularity,
        bvid=bvid,
        aid=aid,
        pubdate=archive.get("pubdate") or item.get("pubdate"),
    )


def parse_ranking_item(item: dict[str, Any], provider_instance: str) -> Track | None:
    """Convert one ranking entry; invalid entries -> None."""
    if not isinstance(item, dict):
        return None
    return _parse_video_item(item, provider_instance)
