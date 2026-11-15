"""
Bilibili UP collections (合集 / UGC season) as Music Assistant playlists.

A collection is Bilibili's ``ugc_season``: a curated, ordered list of videos
owned by one UP.  It maps naturally onto a Music Assistant ``Playlist``:

* ``meta`` (title/cover/description/owner)  -> the Playlist item
* ``archives`` (one entry per video)        -> the Playlist tracks

Discovery is deliberately explicit-only, the same way live rooms work:

* ``BSEASON:<season id>``                  -> that collection
* a copied collection URL                  -> that collection
* ``UP:<mid>`` or the UP's space URL       -> that UP's collections
* searching a ``BV...`` video that belongs to a collection also returns the
  collection next to the track.

A bare number is never treated as a collection id, so ordinary music searches
are unaffected.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from music_assistant_models.errors import MediaNotFoundError

from . import constants, parsers
from .client import BiliApiError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from music_assistant_models.media_items import Playlist, Track

    from .client import BilibiliClient

#: Collection URL: https://space.bilibili.com/<mid>/lists/<season_id>?type=season
#: Music Assistant replaces "/" with " " before a query reaches a provider, so
#: the pattern accepts whitespace as separator as well.
_LISTS_URL_RE = re.compile(r"space\.bilibili\.com\s+\d+\s+lists\s+(\d+)", re.IGNORECASE)
#: Older collection URL: /channel/collectiondetail?sid=<season_id>
_COLLECTION_DETAIL_RE = re.compile(r"collectiondetail\?\S*?\bsid=(\d+)", re.IGNORECASE)
#: Explicit prefixes.
_SEASON_PREFIX_RE = re.compile(r"^bseason\s*:\s*(\d+)$", re.IGNORECASE)
_UP_PREFIX_RE = re.compile(r"^up\s*:\s*(\d+)$", re.IGNORECASE)
#: UP space URL (optionally with a trailing tab, e.g. "/video").
_UP_URL_RE = re.compile(r"^https?\s*:?\s*space\.bilibili\.com\s+(\d+)(?:\s+\S+)*$", re.IGNORECASE)

#: Upper bound for collection list pagination (guards against API loops).
MAX_LIST_PAGES: int = 20


def _normalize(query: str) -> str:
    """
    Collapse whitespace and treat ``/`` as a separator.

    Handles both the raw query and the form Music Assistant passes to providers
    (it replaces ``/`` with a space).
    """
    return " ".join(str(query or "").replace("/", " ").split())


def _int(value: Any, default: int = 0) -> int:
    """Best-effort int conversion for API numbers that may arrive as strings."""
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except TypeError, ValueError:
        try:
            return int(float(value))
        except TypeError, ValueError, OverflowError:
            return default


def season_id_from_query(query: str) -> str | None:
    """Return a collection id when *query* explicitly addresses one."""
    text = _normalize(query)
    if not text:
        return None

    match = _SEASON_PREFIX_RE.match(text)
    if match:
        return match.group(1)

    match = _LISTS_URL_RE.search(text)
    if match:
        # "type=series" addresses a series (not supported here) - ignore it so
        # the query falls back to a normal music search instead.
        if re.search(r"type\s*=\s*series", text, re.IGNORECASE):
            return None
        return match.group(1)

    match = _COLLECTION_DETAIL_RE.search(text)
    if match:
        return match.group(1)
    return None


def up_mid_from_query(query: str) -> str | None:
    """Return an UP mid when *query* explicitly addresses an UP's collections."""
    text = _normalize(query)
    if not text:
        return None

    match = _UP_PREFIX_RE.match(text)
    if match:
        return match.group(1)

    match = _UP_URL_RE.match(text)
    if match:
        return match.group(1)
    return None


def season_id_from_item_id(item_id: str) -> str | None:
    """Return the collection id encoded in a ``season:<id>`` item id."""
    raw = str(item_id or "").strip()
    suffix = (
        raw[len(constants.COLLECTION_PLAYLIST_PREFIX) :]
        if raw.startswith(constants.COLLECTION_PLAYLIST_PREFIX)
        else ""
    )
    return suffix if suffix.isdigit() else None


def season_id_from_view(data: dict[str, Any]) -> str | None:
    """Return the collection id of a ``/x/web-interface/view`` payload, if any."""
    season = data.get("ugc_season")
    if not isinstance(season, dict):
        return None
    season_id = str(season.get("id") or "").strip()
    return season_id if season_id.isdigit() else None


def collection_from_view(data: dict[str, Any]) -> dict[str, Any] | None:
    """
    Return a ``meta``-shaped dict from a view payload's ``ugc_season`` block.

    The view payload has no owner name; the caller fills in ``mid`` only, which
    is enough to look the owner up later.
    """
    season = data.get("ugc_season")
    if not isinstance(season, dict):
        return None
    season_id = str(season.get("id") or "").strip()
    if not season_id.isdigit():
        return None
    return {
        "season_id": season_id,
        "mid": season.get("mid"),
        "title": season.get("title"),
        "cover": season.get("cover"),
        "description": season.get("intro") or season.get("description"),
    }


async def find_collection_meta(
    client: BilibiliClient,
    season_id: str,
) -> dict[str, Any]:
    """Return a collection's ``meta`` block, raising when it does not exist."""
    try:
        data = await client.season_archives(int(season_id), page=1, page_size=1)
    except (BiliApiError, ValueError) as err:
        raise MediaNotFoundError(f"Bilibili collection {season_id} is unavailable") from err

    meta = data.get("meta") if isinstance(data, dict) else None
    if not isinstance(meta, dict) or not str(meta.get("season_id") or "").isdigit():
        raise MediaNotFoundError(f"Bilibili collection {season_id} was not found")
    return meta


async def get_collection(
    client: BilibiliClient,
    provider_instance: str,
    season_id: str,
    owner_name: str = "",
) -> Playlist:
    """Build the Playlist for one collection."""
    meta = await find_collection_meta(client, season_id)
    playlist = parsers.parse_collection(meta, provider_instance, owner_name)
    if playlist is None:
        raise MediaNotFoundError(f"Bilibili collection {season_id} returned no metadata")
    return playlist


async def collection_page(
    client: BilibiliClient,
    provider_instance: str,
    season_id: str,
    page: int,
    owner_name: str = "",
    owner_mid: Any = None,
) -> list[Track]:
    """Return one zero-based page of a collection's tracks."""
    page = max(int(page), 0)
    try:
        data = await client.season_archives(
            int(season_id),
            page=page + 1,
            page_size=constants.COLLECTION_PAGE_SIZE,
        )
    except (BiliApiError, ValueError) as err:
        raise MediaNotFoundError(f"Bilibili collection {season_id} is unavailable") from err

    archives = data.get("archives") if isinstance(data, dict) else None
    tracks: list[Track] = []
    for item in archives or ():
        track = parsers.parse_collection_archive(item, provider_instance, owner_name, owner_mid)
        if track is None:
            continue
        track.position = page * constants.COLLECTION_PAGE_SIZE + len(tracks) + 1
        tracks.append(track)
    return tracks


async def iter_collection_tracks(
    client: BilibiliClient,
    provider_instance: str,
    season_id: str,
    owner_name: str = "",
    owner_mid: Any = None,
    max_tracks: int = 5000,
) -> AsyncIterator[Track]:
    """Yield every playable track of a collection, page by page."""
    page = 0
    yielded = 0
    while page < MAX_LIST_PAGES * 5 and yielded < max_tracks:
        tracks = await collection_page(
            client, provider_instance, season_id, page, owner_name, owner_mid
        )
        if not tracks:
            break
        for track in tracks:
            yield track
            yielded += 1
            if yielded >= max_tracks:
                return
        if len(tracks) < constants.COLLECTION_PAGE_SIZE:
            break
        page += 1


async def list_up_collections(
    client: BilibiliClient,
    provider_instance: str,
    mid: str,
    owner_name: str = "",
) -> list[Playlist]:
    """Return every collection of one UP (series are intentionally skipped)."""
    playlists: list[Playlist] = []
    seen: set[str] = set()
    page = 1
    while page <= MAX_LIST_PAGES:
        try:
            data = await client.space_collections(int(mid), page=page, page_size=20)
        except (BiliApiError, ValueError) as err:
            raise MediaNotFoundError(f"Bilibili UP {mid} collections are unavailable") from err

        items = data.get("items_lists") if isinstance(data, dict) else None
        if not isinstance(items, dict):
            break
        seasons = items.get("seasons_list") or []
        for entry in seasons:
            meta = entry.get("meta") if isinstance(entry, dict) else None
            if not isinstance(meta, dict):
                continue
            playlist = parsers.parse_collection(meta, provider_instance, owner_name)
            if playlist is None or playlist.item_id in seen:
                continue
            seen.add(playlist.item_id)
            playlists.append(playlist)

        page_info = items.get("page") if isinstance(items.get("page"), dict) else {}
        total = _int((page_info or {}).get("total"), 0)
        if not seasons or not total or page * 20 >= total:
            break
        page += 1
    return playlists
