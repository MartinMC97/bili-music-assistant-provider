# Bilibili provider for Music Assistant

[![CI](https://github.com/MartinMC97/bili-music-assistant-provider/actions/workflows/ci.yml/badge.svg)](https://github.com/MartinMC97/bili-music-assistant-provider/actions/workflows/ci.yml)
[![Upstream stable release](https://github.com/MartinMC97/bili-music-assistant-provider/actions/workflows/upstream-stable.yml/badge.svg)](https://github.com/MartinMC97/bili-music-assistant-provider/actions/workflows/upstream-stable.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**English** | [简体中文](README.zh-CN.md)

An unofficial [Music Assistant](https://music-assistant.io) music provider that turns the audio
track of [Bilibili](https://www.bilibili.com) videos into playable tracks.

The provider is read-only and streaming-only: it never downloads media, never modifies your
Bilibili account and never bypasses memberships or paid content. It is intended for personal use;
please follow Bilibili's terms of service.

> Status: alpha. The provider targets the Music Assistant 2.10 stable line. It is not affiliated
> with or endorsed by Bilibili or the Music Assistant project.

## Features

- **Search** the Bilibili video index and get playable tracks. Titles are cleaned of noise tags,
  results are filtered by duration (60–900 seconds by default), de-duplicated and sorted by
  popularity.
- **Direct lookup** by a full `BV...` id or `av<id>`. A video that belongs to an UP collection
  also returns that collection in the playlist section.
- **DASH playback** — AAC 64/132/192 kbps, Hi-Res FLAC or Dolby, depending on the account's
  entitlements and the quality ceiling configured on the provider. Streams are handed to ffmpeg
  with the `Referer`, `Origin` and desktop `User-Agent` headers the CDN requires.
- **Sign-in options**: QR code (recommended), a manual `SESSDATA` cookie (plus `DedeUserID` for
  favorites), or anonymous. An expired credential degrades to anonymous instead of breaking the
  provider.
- **Favorite folders** as read-only playlists (login required, pagination handled), including
  Bilibili audio (`au`) entries. Non-playable entries such as bangumi/OGV are skipped.
- **Home rows and browsing**: music chart, new releases, regional music chart and favorites.
- **UP collections** (合集 / UGC season) exposed as playlists — see
  [UP collections as playlists](#up-collections-as-playlists).
- **Bilibili audio** (`au` tracks) plays through the dedicated audio API. That endpoint usually
  offers a single bitrate, so the DASH quality options do not apply to audio tracks.

## Requirements

| Component | Requirement |
|---|---|
| Music Assistant | 2.10.7 or newer; built and tested against the current stable release (2.10.13) |
| `music-assistant-models` | 1.1.205 or newer (bundled with Music Assistant) |
| Development | Python 3.14 and Docker (the dev container provides Music Assistant and ffmpeg) |

Music Assistant is currently on the 2.10 stable line; 2.11 is still in beta upstream and has not
been validated with this provider yet.

The provider is not a PyPI package. Music Assistant only discovers providers that live in
`music_assistant/providers/<domain>/`, so installing it means copying `provider/bilibili` into the
Music Assistant package (or using one of the images below).

## Installation

A complete step-by-step guide is in **[docs/INSTALL.md](docs/INSTALL.md)** (currently Chinese
only). The short version:

### Option A — use the prebuilt image (recommended)

GitHub Actions publishes Music Assistant images with the provider baked in. The package is public
(no registry login needed) and multi-architecture (`linux/amd64` and `linux/arm64`):

```bash
docker pull ghcr.io/martinmc97/music-assistant:latest
```

Point your existing Music Assistant service at that image and keep the rest of the configuration
unchanged:

```yaml
services:
  music-assistant:
    image: ghcr.io/martinmc97/music-assistant:latest
    container_name: music-assistant
    restart: unless-stopped
    network_mode: host          # Music Assistant discovers players over the host network
    volumes:
      - /path/to/ma-data:/data  # your existing data directory
    environment:
      - LOG_LEVEL=info
```

```bash
docker compose up -d
```

Without compose:

```bash
docker run -d --name music-assistant \
  --network host \
  -v /path/to/ma-data:/data \
  -e LOG_LEVEL=info \
  ghcr.io/martinmc97/music-assistant:latest
```

- The image already contains `provider/bilibili` and `segno` (needed for QR login); nothing has to
  be installed after start-up.
- `latest` follows the newest upstream Music Assistant stable release. To pin a build, use a tag
  such as `2.10.13-bili-<7-character commit>`, for example
  `ghcr.io/martinmc97/music-assistant:2.10.13-bili-11079f7`.
- Pick an architecture explicitly with `docker pull --platform linux/arm64 ...` if needed.
- On Synology/QNAP, put the image reference into Container Manager and map `/data` the same way.

### Option B — build your own image (when changing code)

```bash
git clone https://github.com/MartinMC97/bili-music-assistant-provider.git bilibili-ma-provider
cd bilibili-ma-provider

# MA_VERSION selects the upstream base image; use the version your instance runs.
docker build -f docker/Dockerfile --build-arg MA_VERSION=2.10.13 \
  -t music-assistant:2.10.13-bili .
```

Then replace the `image:` of your existing service with `music-assistant:2.10.13-bili` and keep
the rest of the configuration. The Dockerfile copies the provider into Music Assistant's provider
directory, installs `segno==1.6.6`, and keeps the official entrypoint.

`docker/compose.build.yml` does the same thing in one step for a throw-away instance:

```bash
docker compose -f docker/compose.build.yml up -d
```

### Option C — drop it into a running container (quick test)

```bash
./scripts/install_docker.sh music-assistant   # container name; defaults to "music-assistant"
```

This copies the provider into the container and restarts it. The copy is lost when the container
is re-created or the image is updated, so use Option A or B for a persistent installation.

### Option D — bare metal / virtualenv

```bash
/path/to/ma-venv/bin/python scripts/install_venv.py
```

Run this with the Python interpreter of the Music Assistant environment. The script copies
`provider/bilibili` into that interpreter's `music_assistant/providers` directory and never
downloads anything. Music Assistant upgrades overwrite the package directory, so re-run the
script afterwards.

### After installing

Restart Music Assistant, open **Settings → Providers → Add Provider → Bilibili**, and choose a
sign-in method. QR login is recommended because it also grants access to your favorite folders;
anonymous mode works for search and playback.

Home Assistant OS does not provide a way to inject providers into the Music Assistant add-on, so
the add-on installation is not supported — see [docs/INSTALL.md](docs/INSTALL.md).

### Updating

```bash
docker compose pull music-assistant   # or: docker pull <image>
docker compose up -d
```

Provider updates do not touch the `/data` volume. Music Assistant migrates its own database at
start-up, so back up the data directory before upgrading Music Assistant itself.

## Configuration

All options live in the provider settings; credentials are collected by the setup flow.

| Option | Default | Description |
|---|---|---|
| Audio quality ceiling | `auto` | Best stream the account allows. FLAC and Dolby require a premium membership; AAC 192/132/64 kbps are also available. |
| Minimum duration | 60 s | Keyword-search results shorter than this are dropped. `0` disables the lower bound. |
| Maximum duration | 900 s | Keyword-search results longer than this are dropped (hour-long mixes, courses). `0` disables the upper bound. |
| Search result limit | 20 | Maximum number of tracks returned per search (1–50). |
| Probe CDN before playback | on | Sends a 1 KB range request to the preferred CDN host and falls back to a backup host when it fails. |
| Manual SESSDATA cookie | empty | Fallback sign-in when QR login is unavailable; `DedeUserID` is also required to read favorite folders. |

Sign-in methods:

| Method | What it enables |
|---|---|
| QR code | Full login: higher quality where entitled, favorite folders. Requires `segno`, which the images bundle. |
| Manual `SESSDATA` + `DedeUserID` | Same as QR login, without the optional dependency. |
| Anonymous | Search, charts and playback at anonymous quality; no favorite folders. |

## UP collections as playlists

A Bilibili "collection" (合集, `ugc_season`) is a curated group of videos made by an UP owner.
The provider maps it to a Music Assistant **playlist**. Collections are explicit-only — they never
show up in a keyword search unless you ask for one:

| Entry point | What happens |
|---|---|
| `BSEASON:7817533` | Opens the collection with that id. |
| `https://space.bilibili.com/3546851714271831/lists/7817533?type=season` | Paste a collection link (`channel/collectiondetail?sid=` links work too). |
| `UP:3546851714271831` (or the UP's space URL) | Lists all collections of that UP. |
| Search for `BV1FMsn6REhn` | If the video belongs to a collection, both the track and the collection are returned. |

Collections appear in the **Playlists** section of the search results. Their tracks are paginated
(100 per page), so a 169-video collection loads in two pages.

Known limitations:

- Collections only — "series" lists (`type=series`) and full UP upload lists are not supported.
- Collection tracks play as-is: the search duration filter is not applied, so long mixes work.
- Collections are not added to the Music Assistant library; open them from search or by id.
- A bare numeric id is not treated as a collection, to avoid colliding with track names.

## Prebuilt images and tags

Images are published to:

```text
ghcr.io/martinmc97/music-assistant
```

- Platforms: `linux/amd64`, `linux/arm64`; the package is public.
- Tags: `2.10.13-bili-<7-character commit>` (pinned Music Assistant version + provider build),
  `latest`, `main` and `sha-<sha>`. Git tags `v*` also produce semver tags (`0.1.0`, `0.1`).
- [`docker-publish.yml`](.github/workflows/docker-publish.yml) builds on every push to `main` and
  on `v*` tags. It resolves the newest upstream Music Assistant stable release by default; a
  manual run can request a specific version, and the repository variable `MA_VERSION` is only a
  fallback when the GitHub API is unreachable.
- [`upstream-stable.yml`](.github/workflows/upstream-stable.yml) runs every six hours. When a new
  Music Assistant stable release appears and the matching `<version>-bili-<commit>` tag does not
  exist yet, it runs the test suite against that release and then builds, pushes and moves
  `latest`.

Pinning the current stable build:

```bash
docker pull ghcr.io/martinmc97/music-assistant:2.10.13-bili-11079f7
```

## Known limitations

- No lyrics.
- One track per video: multi-part videos are not expanded into one track per part yet.
- No bangumi/OGV episodes; those entries are skipped in favorite folders.
- Only UP collections are exposed: "series" lists are not supported, and full UP upload lists are
  blocked by Bilibili's anti-abuse limits (in practice rarely reachable).
- Keyword search returns tracks only; playlists come from explicit collection queries and your
  favorite folders.
- Bilibili audio (`au`) tracks usually offer a single bitrate, so FLAC/Dolby options only apply
  to video DASH streams.
- Content availability and quality depend on region, account entitlements and Bilibili's
  anti-abuse limits. Playback URLs expire after a few hours, so a fresh one is resolved for
  every playback.
- The provider never writes to your Bilibili account: favorite folders stay read-only.

## Repository layout

```text
provider/bilibili/   # the provider itself (Music Assistant domain: bilibili)
tests/               # unit tests and recorded API fixtures
docker/              # isolated dev server and image build
scripts/             # development, installation and live E2E helpers
docs/                # contracts, installation guide, plans and dev log
artifacts/           # working notes and code reviews
```

## Local development

The dev container runs an isolated Music Assistant instance and mounts the source tree, so it
never touches an existing installation:

```bash
# Start the isolated server on http://localhost:18095 (data in .dev-data/)
docker compose -f docker/compose.dev.yml up -d

# Restart after source changes and follow the logs
./scripts/dev.sh
```

Create an admin account on first start at <http://localhost:18095>, then add the **Bilibili**
provider in Settings → Providers.

The dev compose mounts the provider into the Music Assistant package inside the container. The
mount path follows the image's Python version (`.../lib/python3.14/site-packages/music_assistant/`);
update it if you change the base image.

Run the same checks as upstream (tool versions are pinned in `pyproject.toml`):

```bash
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/ruff check provider tests
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/ruff format --check provider tests
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python -m mypy
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/codespell provider tests scripts docs README.md README.zh-CN.md
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python scripts/check_provider_conventions.py
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python -m pytest -q
```

`.pre-commit-config.yaml` mirrors these checks locally (`pre-commit install`).

### Live end-to-end checks

These scripts hit the real Bilibili API and CDN (plus ffmpeg) and are not part of the unit suite:

```bash
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python scripts/e2e_stream.py
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python scripts/e2e_collections.py
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python scripts/e2e_audio.py
```

Unit tests mock the network. The live integration tests are opt-in:
`RUN_LIVE=1 python -m pytest -m live tests/integration/`.

## Credits

- [**HanversionOvO/BiliMusic**](https://github.com/HanversionOvO/BiliMusic) — reference for
  Bilibili endpoint behavior, WBI signing, QR login, DASH audio selection and title-cleaning
  ideas.
- [**music-assistant/server**](https://github.com/music-assistant/server) — the provider API,
  `StreamDetails` and the streaming playback model.
- **The public Bilibili web API** — video/audio endpoints and the playback protocol.

> BiliMusic currently has no `LICENSE` file. It was used as a reference for interface behavior
> and algorithms only; no source code was copied. If upstream adds a license, or you believe a
> part of this implementation is too close to theirs, please open an issue and we will adjust or
> rewrite it.

## License

[Apache-2.0](LICENSE); copyright and attribution details are in [NOTICE](NOTICE).

This is an **unofficial project** with no affiliation, authorization or endorsement from
Bilibili. The Apache-2.0 license grants rights to this code only, not to the Bilibili name, logo
or trademarks.

## Disclaimer

This project does not bundle any audio or video content. It is intended for learning, research
and personal use. Follow your local laws and the platform's terms of service, and do not use it
to infringe copyright or bypass platform restrictions.
