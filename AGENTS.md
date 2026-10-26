# Repository Guidelines

## Project Structure & Module Organization

`provider/bilibili/` is the sole source tree for this unofficial Music Assistant provider. `__init__.py` integrates the provider; `client.py` handles HTTP; `parsers.py` maps responses into MA models; `catalog.py` handles browsing and favorites; `auth.py`, `setup_flow.py`, and `wbi.py` handle credentials, login, and signing. Provider assets include `manifest.json`, `strings.json`, and `icon.svg`.

Tests live in `tests/unit/` and `tests/integration/`, with recorded JSON responses in `tests/fixtures/`. `docker/` contains development and image configurations; `scripts/` contains installation and development helpers. Consult `docs/contracts.md` for module boundaries and `docs/INSTALL.md` for installation.

## Build, Test, and Development Commands

Use Python 3.14 and the repository's MA 2.10.8 development container, which supplies MA dependencies:

```bash
# Start the isolated development server at http://localhost:18095
docker compose -f docker/compose.dev.yml up -d
# Restart after source changes and follow logs
./scripts/dev.sh
# Run tests and lint inside the development container
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python -m pytest -q
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/ruff check provider tests
# Build a distributable MA image containing the provider
docker build -f docker/Dockerfile -t music-assistant:2.10.8-bili .
```

## Coding Style & Naming Conventions

Use four-space indentation, type annotations, and short docstrings. Follow existing `snake_case` functions/modules, `PascalCase` classes, and `UPPER_SNAKE_CASE` constants. Ruff targets Python 3.14 with a 100-character line length and checks imports, upgrades, bug patterns, and style. Keep HTTP logic in the client and model conversion in parsers. Reuse `mass.http_session` for provider requests.

## Testing Guidelines

Use pytest and pytest-asyncio with automatic asyncio mode. Name files `test_<module>.py` and cases `test_<behavior>`. Add fixture-backed regression tests for changed behavior and mock network calls in unit tests. No numeric coverage threshold is configured.

Live tests require explicit opt-in: `RUN_LIVE=1 python -m pytest -m live tests/integration/` in a configured Python environment. `python scripts/e2e_stream.py` verifies real streaming with API/CDN access.

## Commit & Pull Request Guidelines

Follow the observed Conventional Commits style: `feat:`, `fix:`, `docs:`, `chore:`, or `ci:`, with optional scopes such as `chore(dev):`. Keep subjects concise and imperative. PRs should describe behavior changes, link relevant issues, and report test/lint results; include screenshots for setup UI changes.

## Security & Configuration

Keep SESSDATA, cookies, tokens, signed playback URLs, and local MA data out of commits and logs. Preserve streaming-only playback, read-only favorites, and account entitlement checks.
