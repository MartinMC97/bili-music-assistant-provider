"""Live end-to-end check for Bilibili live rooms.

Run inside the dev container (it has ffmpeg and the MA models):

    docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python scripts/e2e_live_stream.py

The script walks the real provider path: ``search(<room number>)`` returns a
radio item, ``get_stream_details`` resolves the muxed live FLV URL, and ffmpeg
fetches/decode a few seconds with exactly the input arguments the provider hands
to Music Assistant.  Only hosts and format details are printed, never the signed
URL.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from music_assistant_models.config_entries import ProviderConfig
from music_assistant_models.enums import MediaType, ProviderType
from music_assistant_models.errors import MusicAssistantError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "provider"))

from bilibili import BilibiliProvider  # noqa: E402
from bilibili.auth import BilibiliCredential  # noqa: E402
from bilibili.client import BilibiliClient  # noqa: E402

#: A live room number or a full ``https://live.bilibili.com/<id>`` URL.
ROOM_QUERY = sys.argv[1] if len(sys.argv) > 1 else "6"
LOG = logging.getLogger("e2e-live")


class FakeMass:
    """Only what BilibiliClient/search/get_stream_details touch."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Keep the shared aiohttp session."""
        self.http_session = session


class E2EProvider(BilibiliProvider):
    """A provider instance without running the full MA app."""

    def __init__(self, mass: FakeMass, config: ProviderConfig, client: BilibiliClient) -> None:
        """Register the bare minimum provider state."""
        self.mass = mass
        self.config = config
        self.logger = LOG
        self._client = client
        self._view_cache = {}
        self._cdn_health = {}
        self._artist_names = {}
        self._user_name = ""
        self._mid = 0


async def main() -> int:
    """Run the live room search/stream check."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    async with aiohttp.ClientSession() as session:
        mass = FakeMass(session)
        client = BilibiliClient(mass, BilibiliCredential())
        config = ProviderConfig(
            type=ProviderType.MUSIC,
            domain="bilibili",
            instance_id="e2e-live",
            values={},
        )
        provider = E2EProvider(mass, config, client)

        results = await provider.search(ROOM_QUERY, [MediaType.RADIO], limit=5)
        if not results.radio:
            LOG.error("room %s did not produce a radio result", ROOM_QUERY)
            return 1
        room = results.radio[0]
        print(
            json.dumps(
                {
                    "item_id": room.item_id,
                    "name": room.name,
                    "description": room.metadata.description,
                    "has_cover": bool(room.metadata.images),
                    "url": next(iter(room.provider_mappings)).url,
                },
                ensure_ascii=False,
            )
        )

        try:
            details = await provider.get_stream_details(room.item_id, MediaType.RADIO)
        except MusicAssistantError as err:
            print(json.dumps({"error": str(err)}, ensure_ascii=False))
            return 1
        header_value = details.extra_input_args[1]
        print(
            json.dumps(
                {
                    "host": urlsplit(details.path or "").hostname,
                    "media_type": details.media_type.value,
                    "stream_type": details.stream_type.value,
                    "duration": details.duration,
                    "can_seek": details.can_seek,
                    "expiration": details.expiration,
                    "has_live_referer": "Referer: https://live.bilibili.com" in header_value,
                },
                ensure_ascii=False,
            )
        )

        # The real proof: fetch and decode a few seconds of the live stream with
        # the exact input arguments the provider handed to Music Assistant.
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-headers",
            header_value,
            "-i",
            details.path or "",
            "-t",
            "3",
            "-vn",
            "-f",
            "null",
            "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)  # noqa: S603
        if result.returncode != 0:
            LOG.error("ffmpeg failed: %s", result.stderr[-800:])
            return 1
        print(json.dumps({"ffmpeg": "ok", "decoded_seconds": 3}))
        await client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
