"""Favorites, recommendations and browse helpers for the Bilibili provider.

The catalog layer only translates client payloads into Music Assistant models
through the helpers in :mod:`bilibili.parsers`; it never builds models from raw
API dictionaries itself.  A malformed list entry is skipped (and logged at
debug level) so that one bad video never breaks a whole favorite folder or
recommendation row.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable, Sequence
from typing import TYPE_CHECKING, Any, Final

from music_assistant_models.errors import MediaNotFoundError
from music_assistant_models.media_items import (
    BrowseFolder,
    ItemMapping,
    MediaItemType,
    Playlist,
    RecommendationFolder,
    Track,
    UniqueList,
)

from .parsers import (
    parse_fav_folder,
    parse_fav_item,
    parse_music_center_item,
    parse_ranking_item,
)

if TYPE_CHECKING:
    from .client import BilibiliClient

_LOGGER: Final = logging.getLogger(__name__)

#: Bilibili exposes favorite folders as ``ml<media_id>``; MA ids add a prefix.
FAV_PLAYLIST_PREFIX: Final = "fav:"
FAV_PAGE_SIZE: Final = 20

#: Safety net against an API that keeps claiming ``has_more`` forever.
MAX_COLLECTED_PAGES: Final = 50

RANK_MUSIC_ROW: Final = "rank_music"
NEW_MUSIC_ROW: Final = "new_music"
REGION_MUSIC_ROW: Final = "region_music"

BROWSE_RANK: Final = "rank"
BROWSE_NEW: Final = "new"
BROWSE_FAVORITES: Final = "favorites"

#: (item_id, display name, material design icon) for the home screen rows.
_RECOMMENDATION_ROWS: Final = (
    (RANK_MUSIC_ROW, "音乐综合榜", "mdi-chart-line"),
    (NEW_MUSIC_ROW, "新歌", "mdi-new-box"),
    (REGION_MUSIC_ROW, "分区榜", "mdi-map-music"),
)

#: (item_id/path, display name) for the browse tree children.
_BROWSE_FOLDERS: Final = (
    (BROWSE_RANK, "排行榜"),
    (BROWSE_NEW, "新歌"),
    (BROWSE_FAVORITES, "收藏夹"),
)

# The music region on Bilibili (rid=3 is 音乐).
_MUSIC_REGION_ID: Final = 3


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _parse_playlist_id(playlist_id: str) -> int:
    """Parse a ``fav:<media_id>`` id, raising MediaNotFoundError when invalid."""
    raw = str(playlist_id or "").strip()
    message = f"Invalid Bilibili favorite playlist id: {playlist_id!r}"
    if not raw.startswith(FAV_PLAYLIST_PREFIX):
        raise MediaNotFoundError(message)
    try:
        media_id = int(raw[len(FAV_PLAYLIST_PREFIX) :])
    except TypeError, ValueError:
        raise MediaNotFoundError(message) from None
    if media_id <= 0:
        raise MediaNotFoundError(message)
    return media_id


def _folder_owner(item: Any) -> str:
    """Return the folder owner name when the API includes one."""
    if not isinstance(item, dict):
        return ""
    for key in ("upper", "owner"):
        value = item.get(key)
        if isinstance(value, dict):
            name = str(value.get("name") or "").strip()
            if name:
                return name
    return str(item.get("owner_name") or "").strip()


def _parse_folder(item: Any, provider_instance: str) -> Playlist | None:
    """Convert one favorite folder entry, skipping malformed payloads."""
    try:
        return parse_fav_folder(item, provider_instance, _folder_owner(item))
    except Exception:  # One malformed folder must not break the list.
        _LOGGER.debug("Skipping malformed Bilibili favorite folder entry", exc_info=True)
        return None


def _parse_tracks(
    items: Sequence[Any],
    parser: Callable[[dict[str, Any], str], Track | None],
    provider_instance: str,
) -> UniqueList[Track]:
    """Convert list entries with *parser*, skipping failures and duplicates."""
    tracks: UniqueList[Track] = UniqueList()
    for item in items:
        try:
            track = parser(item, provider_instance)
        except Exception:  # One malformed item must not break the page.
            _LOGGER.debug("Skipping malformed Bilibili media entry", exc_info=True)
            continue
        if track is not None:
            tracks.append(track)
    return tracks


def _as_list(value: Any) -> list[Any]:
    """Return *value* when it is a list, otherwise an empty list."""
    return value if isinstance(value, list) else []


def _browse_key(path: str) -> str:
    """Normalise ``bilibili://rank`` / ``rank`` / ``/rank/`` to ``rank``."""
    key = str(path or "").strip()
    if "://" in key:
        key = key.split("://", 1)[1]
    return key.strip("/")


# ---------------------------------------------------------------------------
# favorites
# ---------------------------------------------------------------------------
async def iter_favorite_playlists(
    client: BilibiliClient, provider_instance: str
) -> AsyncIterator[Playlist]:
    """Yield the user's own and collected favorite folders.

    Anonymous credentials yield nothing at all (no request is made) so the
    library can load normally without login.
    """
    credential = getattr(client, "credential", None)
    if credential is None or not credential.has_login:
        return
    mid = getattr(credential, "mid", 0)
    if not mid:
        _LOGGER.debug("Bilibili credential has no usable mid; cannot list favorite folders")
        return

    seen: set[str] = set()

    created = await client.fav_folders(mid)
    for item in _as_list(created):
        playlist = _parse_folder(item, provider_instance)
        if playlist is None or playlist.item_id in seen:
            continue
        seen.add(playlist.item_id)
        yield playlist

    # Collected folders are paginated by the API; keep going while has_more.
    page = 1
    while page <= MAX_COLLECTED_PAGES:
        payload = await client.fav_collected(mid, page, FAV_PAGE_SIZE)
        entries = _as_list(payload.get("list")) if isinstance(payload, dict) else _as_list(payload)
        for item in entries:
            playlist = _parse_folder(item, provider_instance)
            if playlist is None or playlist.item_id in seen:
                continue
            seen.add(playlist.item_id)
            yield playlist

        has_more = bool(payload.get("has_more")) if isinstance(payload, dict) else False
        if not has_more or not entries:
            break
        page += 1


async def get_favorite_playlist(
    client: BilibiliClient, provider_instance: str, playlist_id: str
) -> Playlist:
    """Return one favorite folder by its ``fav:<media_id>`` id."""
    wanted = f"{FAV_PLAYLIST_PREFIX}{_parse_playlist_id(playlist_id)}"
    async for playlist in iter_favorite_playlists(client, provider_instance):
        if playlist.item_id == wanted:
            return playlist
    raise MediaNotFoundError(f"Bilibili favorite playlist not found: {playlist_id!r}")


async def get_favorite_tracks(
    client: BilibiliClient,
    provider_instance: str,
    playlist_id: str,
    page: int = 0,
) -> list[Track]:
    """Return one page (``page`` is zero based) of a favorite folder's tracks.

    The provider loops over pages itself; this function only fetches the
    requested page.  Positions start at ``page * 20 + 1`` and stay contiguous
    for the returned tracks (skipped entries do not consume a position).
    """
    media_id = _parse_playlist_id(playlist_id)
    page = max(int(page), 0)

    payload = await client.fav_items(media_id, page + 1, FAV_PAGE_SIZE)
    medias = payload.get("medias") if isinstance(payload, dict) else None

    tracks: list[Track] = []
    base = page * FAV_PAGE_SIZE
    for item in _as_list(medias):
        try:
            track = parse_fav_item(item, provider_instance)
        except Exception:  # One broken item must not break the page.
            _LOGGER.debug("Skipping malformed Bilibili favorite item", exc_info=True)
            continue
        if track is None:
            continue
        track.position = base + len(tracks) + 1
        tracks.append(track)
    return tracks


async def iter_favorite_tracks(
    client: BilibiliClient,
    provider_instance: str,
    playlist_id: str,
    max_tracks: int = 2000,
) -> AsyncIterator[Track]:
    """Yield every playable track of a favorite folder, skipping empty pages.

    MA stops iterating a playlist on the first empty page, so an all-invalid
    page must not terminate the listing. Positions are assigned globally and
    stay contiguous across page boundaries.
    """
    media_id = _parse_playlist_id(playlist_id)
    page = 1
    position = 0
    while page <= MAX_COLLECTED_PAGES and position < max_tracks:
        payload = await client.fav_items(media_id, page, FAV_PAGE_SIZE)
        medias = _as_list(payload.get("medias")) if isinstance(payload, dict) else []
        for item in medias:
            try:
                track = parse_fav_item(item, provider_instance)
            except Exception:  # One broken item must not break the folder.
                _LOGGER.debug("Skipping malformed Bilibili favorite item", exc_info=True)
                continue
            if track is None:
                continue
            position += 1
            track.position = position
            yield track
            if position >= max_tracks:
                break
        has_more = bool(payload.get("has_more")) if isinstance(payload, dict) else False
        if not has_more or not medias:
            break
        page += 1


# ---------------------------------------------------------------------------
# recommendations
# ---------------------------------------------------------------------------
def recommendation_rows(provider_instance: str) -> list[RecommendationFolder]:
    """Return the static descriptors for the home screen recommendation rows."""
    return [
        RecommendationFolder(
            item_id=item_id,
            provider=provider_instance,
            name=name,
            icon=icon,
        )
        for item_id, name, icon in _RECOMMENDATION_ROWS
    ]


async def recommendation_items(
    client: BilibiliClient, provider_instance: str, row_id: str
) -> UniqueList[MediaItemType | ItemMapping | BrowseFolder]:
    """Return the tracks of one recommendation row."""
    row = str(row_id or "").strip()
    if row == RANK_MUSIC_ROW:
        items = await client.music_rank()
        parser = parse_music_center_item
    elif row == NEW_MUSIC_ROW:
        items = await client.new_music()
        parser = parse_music_center_item
    elif row == REGION_MUSIC_ROW:
        items = await client.ranking_region(rid=_MUSIC_REGION_ID)
        parser = parse_ranking_item
    else:
        raise MediaNotFoundError(f"Unknown Bilibili recommendation row: {row_id!r}")

    return _parse_tracks(_as_list(items), parser, provider_instance)


# ---------------------------------------------------------------------------
# browse
# ---------------------------------------------------------------------------
async def browse_items(
    client: BilibiliClient, provider_instance: str, path: str
) -> Sequence[MediaItemType | ItemMapping | BrowseFolder]:
    """Return the browse tree.

    The root (empty path or ``bilibili://``) lists the three folders; the
    ``rank``, ``new`` and ``favorites`` children return their contents.
    """
    key = _browse_key(path)

    if not key:
        return [
            BrowseFolder(
                item_id=item_id,
                provider=provider_instance,
                name=name,
                # MA's music/browse command expects "<instance>://<sub_path>";
                # a bare key makes the controller fail to unpack the path.
                path=f"{provider_instance}://{item_id}",
            )
            for item_id, name in _BROWSE_FOLDERS
        ]

    if key == BROWSE_RANK:
        return _parse_tracks(
            _as_list(await client.music_rank()), parse_music_center_item, provider_instance
        )

    if key == BROWSE_NEW:
        return _parse_tracks(
            _as_list(await client.new_music()), parse_music_center_item, provider_instance
        )

    if key == BROWSE_FAVORITES:
        playlists: UniqueList[Playlist] = UniqueList()
        async for playlist in iter_favorite_playlists(client, provider_instance):
            playlists.append(playlist)
        return playlists

    raise MediaNotFoundError(f"Unknown Bilibili browse path: {path!r}")
