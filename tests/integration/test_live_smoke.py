"""Live smoke test against the real Bilibili API.

Excluded from normal runs: the module is skipped unless ``RUN_LIVE=1`` is set in
the environment. It walks the same path as the provider (search -> view ->
playurl) and finally performs the 1 KB ``Range`` request that ffmpeg relies on.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest
from bilibili import constants
from bilibili.auth import BilibiliCredential
from bilibili.client import BilibiliClient

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE") != "1",
        reason="live test; set RUN_LIVE=1 to hit the real Bilibili API",
    ),
]

SEARCH_KEYWORD = "晴天"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
RANGE_HEADERS = {
    "Referer": constants.BILI_REFERER,
    "Origin": constants.BILI_REFERER,
    "User-Agent": constants.USER_AGENT,
    "Range": "bytes=0-1023",
}


def as_int(value: Any, default: int = 0) -> int:
    """Best-effort int conversion for API fields that may be strings or None."""
    try:
        return int(value)
    except TypeError, ValueError:
        return default


def pick_audio_url(dash: dict[str, Any]) -> str:
    """Pick the best ``dash.audio`` URL using the provider's preference ranking."""
    audio = dash.get("audio") or []
    assert audio, "playurl returned no dash.audio candidates"
    best = max(
        audio,
        key=lambda entry: (
            constants.AUDIO_ID_RANK.get(as_int(entry.get("id")), 0),
            as_int(entry.get("bandwidth")),
        ),
    )
    url = best.get("baseUrl") or best.get("base_url") or best.get("url")
    assert isinstance(url, str) and url, "the selected audio candidate has no URL"
    return url


async def test_live_search_view_playurl_and_range() -> None:
    """Search, resolve a CID, fetch DASH audio and range-request the CDN URL."""
    async with aiohttp.ClientSession() as session:
        client = BilibiliClient(
            SimpleNamespace(http_session=session),
            BilibiliCredential(),
        )

        items = await client.search_video(SEARCH_KEYWORD, page=1, page_size=20)
        videos = [item for item in items if item.get("bvid")]
        assert videos, f"search for {SEARCH_KEYWORD!r} returned no videos"
        bvid = str(videos[0]["bvid"])

        view = await client.view(bvid)
        pages = view.get("pages") or []
        assert pages, f"view for {bvid} returned no pages"
        cid = as_int(pages[0].get("cid"))
        assert cid, f"view for {bvid} returned no usable cid"

        data = await client.playurl(bvid=bvid, cid=cid)
        dash = data.get("dash")
        assert isinstance(dash, dict), f"playurl for {bvid} returned no DASH payload"
        url = pick_audio_url(dash)

        async with session.get(url, headers=RANGE_HEADERS, timeout=REQUEST_TIMEOUT) as response:
            assert response.status in (200, 206), (
                f"CDN range request for {bvid} returned HTTP {response.status}"
            )
