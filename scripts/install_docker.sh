#!/usr/bin/env bash
# Quick install into a *running* Music Assistant container.
#
#   ./scripts/install_docker.sh [container-name] [--no-segno] [--force]
#
# This copies provider/bilibili into the container's provider directory and
# restarts the container. It is intentionally the "quick test" path: a
# `docker compose up --force-recreate` or an image update will drop the copy.
# For a persistent install use docker/Dockerfile or a bind mount (docs/INSTALL.md).
set -euo pipefail

cd "$(dirname "$0")/.."

CONTAINER="${1:-music-assistant}"
shift || true
WITH_SEGNO=1
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --no-segno) WITH_SEGNO=0 ;;
    --force) FORCE=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "Container not found: $CONTAINER" >&2
  echo "Usage: $0 [container-name] [--no-segno] [--force]" >&2
  exit 1
fi

PYBIN=/app/venv/bin/python
# Run through sh: some images put a Python `test` module entry point on PATH,
# which would shadow the shell builtin for `docker exec ... test -x`.
if ! docker exec "$CONTAINER" sh -c "test -x '$PYBIN'" 2>/dev/null; then
  PYBIN=python3
fi

PROVIDERS="$(docker exec "$CONTAINER" "$PYBIN" -c \
  'import music_assistant, os; print(os.path.join(os.path.dirname(music_assistant.__file__), "providers"))')"
TARGET="$PROVIDERS/bilibili"

echo "Container:            $CONTAINER"
echo "Music Assistant path: $PROVIDERS"

if docker exec "$CONTAINER" sh -c "test -e '$TARGET'"; then
  if [ "$FORCE" -ne 1 ]; then
    echo "Provider already exists at $TARGET; use --force to replace it." >&2
    exit 1
  fi
  docker exec "$CONTAINER" sh -c "rm -rf '$TARGET'"
fi

docker cp provider/bilibili "$CONTAINER:$TARGET"

if [ "$WITH_SEGNO" -eq 1 ]; then
  if docker exec "$CONTAINER" sh -c "test -x /app/venv/bin/uv"; then
    docker exec "$CONTAINER" /app/venv/bin/uv pip install --no-cache segno==1.6.6
  else
    docker exec "$CONTAINER" "$PYBIN" -m pip install segno==1.6.6
  fi
fi

docker restart "$CONTAINER" >/dev/null
echo
echo "Installed and restarted $CONTAINER."
echo "Open Music Assistant -> Settings -> Providers -> Add Provider -> Bilibili."
echo "NOTE: this copy lives in the container layer; a recreate/update will remove it."
echo "      Use docker/Dockerfile or a bind mount for a persistent install."
