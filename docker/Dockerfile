# Build a Music Assistant image with the Bilibili provider baked in.
# Usage: docker build -f docker/Dockerfile -t music-assistant:2.10.8-bili .
# Override the base MA version with --build-arg MA_VERSION=x.y.z
ARG MA_VERSION=2.10.8
FROM ghcr.io/music-assistant/server:${MA_VERSION}

# The MA package lives in the venv's site-packages, which depends on the Python
# version of the base image; locate it at build time instead of hardcoding it.
COPY provider/bilibili /tmp/bilibili
RUN PROVIDERS="$(/app/venv/bin/python -c \
        'import music_assistant, os; print(os.path.join(os.path.dirname(music_assistant.__file__), "providers"))')" \
    && mkdir -p "$PROVIDERS" \
    && cp -r /tmp/bilibili "$PROVIDERS/bilibili" \
    && rm -rf /tmp/bilibili \
    && /app/venv/bin/uv pip install --no-cache segno==1.6.6
