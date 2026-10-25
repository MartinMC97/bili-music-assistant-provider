"""Live end-to-end check: BilibiliProvider.get_stream_details() -> ffmpeg.

Run inside the dev container (it has ffmpeg and the MA models):

    docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python scripts/e2e_stream.py

This hits the real Bilibili API and a real CDN; it is not part of the unit test
suite. It prints only hosts and format details, never the signed URL.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "provider"))

from bilibili import BilibiliProvider  # noqa: E402
from bilibili.auth import BilibiliCredential  # noqa: E402
from bilibili.client import BilibiliClient  # noqa: E402

BVID = "BV1GJ411x7h7"
CID = 137649199
LOG = logging.getLogger("e2e")


class FakeMass:
    """Only what BilibiliClient/get_stream_details touch."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.http_session = session


class E2EProvider(BilibiliProvider):
    """A provider instance without running the full MA app."""

    def __init__(self, mass: FakeMass, config: ProviderConfig, client: BilibiliClient) -> None:
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
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s %(message)s")
    async with aiohttp.ClientSession() as session:
        mass = FakeMass(session)
        client = BilibiliClient(mass, BilibiliCredential())
        config = ProviderConfig(
            type=ProviderType.MUSIC,
            domain="bilibili",
            instance_id="e2e",
            values={},
        )
        provider = E2EProvider(mass, config, client)

        details = await provider.get_stream_details(f"{BVID}#{CID}", MediaType.TRACK)
        host = urlsplit(details.path or "").hostname
        header_value = details.extra_input_args[1]
        print(
            json.dumps(
                {
                    "host": host,
                    "codec": details.audio_format.codec_type.value,
                    "container": details.audio_format.content_type.value,
                    "sample_rate": details.audio_format.sample_rate,
                    "channels": details.audio_format.channels,
                    "expiration": details.expiration,
                    "can_seek": details.can_seek,
                    "has_referer": "Referer: https://www.bilibili.com" in header_value,
                    "has_ua": "User-Agent:" in header_value,
                },
                ensure_ascii=False,
            )
        )

        # The real proof: let ffmpeg fetch and decode 2 seconds with the exact
        # input arguments the provider handed to Music Assistant.
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
            "2",
            "-f",
            "null",
            "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)  # noqa: S603
        if result.returncode != 0:
            LOG.error("ffmpeg failed: %s", result.stderr[-800:])
            return 1
        print(json.dumps({"ffmpeg": "ok"}))
        await client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
