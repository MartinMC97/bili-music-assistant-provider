#!/usr/bin/env bash
# Restart the local dev container and follow its logs.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose -f docker/compose.dev.yml restart music-assistant
docker compose -f docker/compose.dev.yml logs -f --tail=120 music-assistant
