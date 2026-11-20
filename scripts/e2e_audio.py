"""Live end-to-end check: BilibiliProvider audio (au) track -> ffmpeg.

Run inside the dev container (it has ffmpeg and the MA models):

    docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python scripts/e2e_audio.py [song_id]

The Bilibili audio endpoints work anonymously, so this needs no credentials. It
prints only hosts and format details, never the signed stream URL.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import aiohttp
from music_assistant_models.config_entries import ProviderConfig
from music_assistant_models.enums import MediaType, ProviderType

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "provider"))

from bilibili import BilibiliProvider  # noqa: E402
from bilibili.auth import BilibiliCredential  # noqa: E402
from bilibili.client import BilibiliClient  # noqa: E402
from e2e_support import E2EMass  # noqa: E402

SONG_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 2533988
LOG = logging.getLogger("e2e-audio")
MANIFEST = SimpleNamespace(type=ProviderType.MUSIC, domain="bilibili", name="Bilibili")


class E2EProvider(BilibiliProvider):
    """A provider instance without running the full MA app."""

    def __init__(self, mass: E2EMass, config: ProviderConfig, client: BilibiliClient) -> None:
        """Register the provider state plus the injected client."""
        super().__init__(mass, MANIFEST, config)
        self._client = client
        self.logger = LOG


async def main() -> int:
    """Resolve one audio track, fetch its stream and decode a few seconds."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    async with aiohttp.ClientSession() as session:
        mass = E2EMass(session)
        client = BilibiliClient(mass, BilibiliCredential())
        config = ProviderConfig(
            type=ProviderType.MUSIC,
            domain="bilibili",
            instance_id="e2e-audio",
            values={},
        )
        provider = E2EProvider(mass, config, client)

        track = await provider.get_track(f"au:{SONG_ID}")
        print(
            json.dumps(
                {
                    "track": track.item_id,
                    "name": track.name,
                    "duration": track.duration,
                    "artist": track.artists[0].name if track.artists else None,
                },
                ensure_ascii=False,
            )
        )

        details = await provider.get_stream_details(f"au:{SONG_ID}", MediaType.TRACK)
        audio_format = details.audio_format
        print(
            json.dumps(
                {
                    "host": urlsplit(details.path or "").hostname,
                    "content_type": audio_format.content_type.value,
                    "codec": audio_format.codec_type.value,
                    "bit_rate": audio_format.bit_rate,
                    "sample_rate": audio_format.sample_rate,
                    "duration": details.duration,
                    "expiration": details.expiration,
                    "has_referer": "Referer" in details.extra_input_args[1],
                }
            )
        )

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            *details.extra_input_args,
            "-i",
            details.path,
            "-t",
            "3",
            "-f",
            "null",
            "-",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        print(json.dumps({"ffmpeg": "ok" if proc.returncode == 0 else "failed"}))
        return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
