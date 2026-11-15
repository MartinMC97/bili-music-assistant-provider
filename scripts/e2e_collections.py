"""Live end-to-end check for Bilibili UP collections (合集).

Run inside the dev container (it has ffmpeg and the MA models):

    docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python scripts/e2e_collections.py

The script walks the real provider path: ``search("BSEASON:<id>")`` resolves a
collection playlist, its tracks are paginated, a track's stream is resolved and
ffmpeg decodes a few seconds with the exact input arguments handed to Music
Assistant.  Only hosts/counts are printed, never signed URLs.
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

SID = sys.argv[1] if len(sys.argv) > 1 else "7817533"
MID = sys.argv[2] if len(sys.argv) > 2 else "3546851714271831"
LOG = logging.getLogger("e2e-collections")
MANIFEST = SimpleNamespace(type=ProviderType.MUSIC, domain="bilibili", name="Bilibili")


class E2EProvider(BilibiliProvider):
    """A provider instance without running the full MA app."""

    def __init__(self, mass: E2EMass, config: ProviderConfig, client: BilibiliClient) -> None:
        """Register the provider state plus the injected client."""
        super().__init__(mass, MANIFEST, config)
        self._client = client
        self.logger = LOG


async def main() -> int:
    """Run the collection search/playlist/stream checks."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    async with aiohttp.ClientSession() as session:
        mass = E2EMass(session)
        client = BilibiliClient(mass, BilibiliCredential())
        config = ProviderConfig(
            type=ProviderType.MUSIC,
            domain="bilibili",
            instance_id="e2e-collections",
            values={},
        )
        provider = E2EProvider(mass, config, client)

        # 1) explicit collection search
        results = await provider.search(f"BSEASON:{SID}", [MediaType.PLAYLIST])
        if not results.playlists:
            LOG.error("BSEASON:%s did not return a playlist", SID)
            return 1
        playlist = results.playlists[0]
        print(
            json.dumps(
                {
                    "playlist": playlist.item_id,
                    "name": playlist.name,
                    "owner": playlist.owner,
                    "has_cover": bool(playlist.metadata.images),
                    "description": (playlist.metadata.description or "")[:40],
                    "url": next(iter(playlist.provider_mappings)).url,
                },
                ensure_ascii=False,
            )
        )

        # 2) the UP's collection list
        up_results = await provider.search(f"UP:{MID}", [MediaType.PLAYLIST])
        print(
            json.dumps(
                {
                    "up_collections": len(up_results.playlists),
                    "first": [p.name[:28] for p in up_results.playlists[:2]],
                },
                ensure_ascii=False,
            )
        )

        # 3) track pagination
        page0 = await provider.get_playlist_tracks(f"season:{SID}", page=0)
        page1 = await provider.get_playlist_tracks(f"season:{SID}", page=1)
        print(
            json.dumps(
                {
                    "page0": len(page0),
                    "page1": len(page1),
                    "first_track": page0[0].item_id if page0 else None,
                    "first_name": (page0[0].name[:40] if page0 else None),
                    "first_position": page0[0].position if page0 else None,
                    "last_position": page1[-1].position if page1 else None,
                },
                ensure_ascii=False,
            )
        )

        # 4) BV search also surfaces the collection
        if page0:
            bv_results = await provider.search(
                page0[0].item_id, [MediaType.TRACK, MediaType.PLAYLIST]
            )
            print(
                json.dumps(
                    {
                        "bv_tracks": len(bv_results.tracks),
                        "bv_collections": [p.item_id for p in bv_results.playlists],
                    }
                )
            )

            # 5) real playback of the first track
            details = await provider.get_stream_details(page0[0].item_id, MediaType.TRACK)
            print(
                json.dumps(
                    {
                        "stream_host": urlsplit(details.path or "").hostname,
                        "codec": details.audio_format.codec_type.value,
                        "duration": details.duration,
                    }
                )
            )
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                *details.extra_input_args,
                "-i",
                details.path or "",
                "-t",
                "3",
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
