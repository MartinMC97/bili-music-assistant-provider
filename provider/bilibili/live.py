"""Live room support for the Bilibili provider.

Live rooms are exposed as :class:`~music_assistant_models.media_items.Radio`
items.  They are deliberately kept out of the regular track search: only an
explicit room number (``21452505``) or a live room URL turns a query into a
radio result, so live streams never mix into music results, recommendations or
the browse tree.

Bilibili does not publish an audio-only rendition for live rooms, so the
provider hands the muxed FLV/HLS URL to ffmpeg and lets it keep the audio
track only.  That is also why live playback is realtime, unseekable and
duration-less from Music Assistant's point of view.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from music_assistant_models.enums import ContentType, MediaType, StreamType
from music_assistant_models.errors import (
    MediaNotFoundError,
    ResourceTemporarilyUnavailable,
    UnplayableMediaError,
)
from music_assistant_models.media_items import AudioFormat, Radio
from music_assistant_models.streamdetails import StreamDetails

from . import constants, parsers
from .client import BiliApiError

if TYPE_CHECKING:
    from .client import BilibiliClient

_ROOM_URL_RE = re.compile(r"live\.bilibili\.com/(?:h5/)?(\d{1,10})", re.IGNORECASE)
_ITEM_ID_RE = re.compile(rf"^{re.escape(constants.LIVE_ITEM_PREFIX)}(\d{{1,10}})$")

#: ``/room/v1/Room/get_info`` answers code ``1`` for unknown or malformed rooms.
_ROOM_NOT_FOUND_CODE: int = 1

#: Bilibili risk control; retrying immediately only makes it worse.
_RISK_CONTROL_CODE: int = -352


def room_id_from_query(query: str) -> str | None:
    """Return the room id when *query* is a room number or a live room URL.

    ``None`` means the query is an ordinary music search and must be left alone.
    """
    text = str(query or "").strip()
    if not text:
        return None

    match = _ROOM_URL_RE.search(text)
    if match:
        return match.group(1)

    if text.startswith(constants.LIVE_ITEM_PREFIX):
        text = text[len(constants.LIVE_ITEM_PREFIX) :]

    if text.isdigit() and len(text) <= constants.LIVE_ROOM_ID_MAX_LENGTH:
        return text
    return None


def room_id_from_item_id(item_id: str) -> str | None:
    """Return the room id encoded in a ``live:<room_id>`` item id."""
    match = _ITEM_ID_RE.match(str(item_id or "").strip())
    return match.group(1) if match else None


def _translate_error(err: BiliApiError, room_id: str) -> Exception:
    """Map a live API error onto the provider error surface."""
    if err.code == _RISK_CONTROL_CODE:
        return ResourceTemporarilyUnavailable(
            "Bilibili live API rejected the request (risk control)",
            backoff_time=60,
        )
    return ResourceTemporarilyUnavailable(
        f"Bilibili live API error {err.code} for room {room_id}",
        backoff_time=10,
    )


async def find_room(
    client: BilibiliClient,
    provider_instance: str,
    query: str,
) -> Radio | None:
    """Return the live room for *query*, or ``None`` when it is not a room query.

    An unknown room also yields ``None`` so the caller can fall back to the
    regular music search (e.g. a numeric-looking song title).
    """
    room_id = room_id_from_query(query)
    if room_id is None:
        return None

    try:
        data = await client.live_room_info(room_id)
    except BiliApiError as err:
        if err.code == _ROOM_NOT_FOUND_CODE:
            return None
        raise _translate_error(err, room_id) from err

    return parsers.parse_live_room(data, provider_instance)


async def get_room(
    client: BilibiliClient,
    provider_instance: str,
    item_id: str,
) -> Radio:
    """Return the live room addressed by a ``live:<room_id>`` item id."""
    room_id = room_id_from_item_id(item_id)
    if room_id is None:
        raise MediaNotFoundError(f"Invalid Bilibili live room id: {item_id}")

    try:
        data = await client.live_room_info(room_id)
    except BiliApiError as err:
        if err.code == _ROOM_NOT_FOUND_CODE:
            raise MediaNotFoundError(f"Bilibili live room {room_id} does not exist") from err
        raise _translate_error(err, room_id) from err

    room = parsers.parse_live_room(data, provider_instance)
    if room is None:
        raise MediaNotFoundError(f"Bilibili live room {room_id} returned no metadata")
    return room


async def get_stream_details(
    client: BilibiliClient,
    provider_instance: str,
    item_id: str,
) -> StreamDetails:
    """Resolve the live stream URL for a ``live:<room_id>`` item id."""
    room_id = room_id_from_item_id(item_id)
    if room_id is None:
        raise MediaNotFoundError(f"Invalid Bilibili live room id: {item_id}")

    try:
        info = await client.live_room_info(room_id)
    except BiliApiError as err:
        if err.code == _ROOM_NOT_FOUND_CODE:
            raise MediaNotFoundError(f"Bilibili live room {room_id} does not exist") from err
        raise _translate_error(err, room_id) from err

    live_status = _live_status(info)
    if live_status != constants.LIVE_STATUS_LIVE:
        state = _status_text(live_status)
        raise MediaNotFoundError(
            f"Bilibili live room {room_id} is not live right now（{state}）"
        )

    try:
        play = await client.live_playurl(room_id)
    except BiliApiError as err:
        raise _translate_error(err, room_id) from err

    stream = parsers.pick_live_stream(play)
    if stream is None:
        raise UnplayableMediaError(
            f"Bilibili live room {room_id} did not return a playable stream "
            "(the room may require login or paid access)"
        )

    return StreamDetails(
        provider=provider_instance,
        item_id=item_id,
        audio_format=AudioFormat(content_type=ContentType.UNKNOWN),
        media_type=MediaType.RADIO,
        stream_type=StreamType.HTTP,
        path=stream.url,
        duration=None,
        can_seek=False,
        allow_seek=False,
        expiration=constants.LIVE_STREAM_EXPIRATION,
        extra_input_args=list(constants.FFMPEG_LIVE_HEADERS),
        data={"protocol": stream.protocol, "format": stream.format, "codec": stream.codec},
    )


def _live_status(info: dict[str, object] | None) -> int:
    """Return the numeric ``live_status`` of a room info payload."""
    if not isinstance(info, dict):
        return constants.LIVE_STATUS_OFFLINE
    value = info.get("live_status")
    if isinstance(value, bool) or not isinstance(value, int | str):
        return constants.LIVE_STATUS_OFFLINE
    try:
        return int(value)
    except ValueError, TypeError:
        return constants.LIVE_STATUS_OFFLINE


def _status_text(status: int) -> str:
    """Return a human readable ``live_status`` label."""
    return {
        constants.LIVE_STATUS_LIVE: "直播中",
        constants.LIVE_STATUS_ROUND: "轮播",
        constants.LIVE_STATUS_OFFLINE: "未开播",
    }.get(status, "未知状态")
