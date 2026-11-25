# 对齐 music-assistant/server 的检查（v2）

目标：让本仓库的代码风格与测试达到 **music-assistant/server 2.10.11** 的合并标准，
方便后续以 provider 形式并入官方仓库（`music_assistant/providers/bilibili/` + `tests/providers/bilibili/`）。

## 已对齐（CI 强制执行）

| 工具 / 检查 | 版本 / 来源 | 状态 |
|---|---|---|
| `ruff check`（`select=["ALL"]` + D213、line 100、py314、pydocstyle、mccabe、pylint、isort） | ruff 0.15.22（官方 pin） | ✅ 0 findings |
| `ruff format --check` | 同上 | ✅ 全绿 |
| `mypy` strict（`disallow_untyped_defs` 等，`files = provider/bilibili + tests`） | mypy 2.3.0（官方 pin） | ✅ 0 errors |
| `codespell` | 2.4.3（官方 pin） | ✅ 全绿 |
| provider conventions（本地复刻官方 4 个脚本，见下） | `scripts/check_provider_conventions.py` | ✅ 全绿 |
| `pytest`（离线，`tests/providers/bilibili/`） | pytest + pytest-asyncio | ✅ 224 passed, 1 skipped |

`provider conventions` 复刻了官方 pre-commit 里的四条规则：

1. **check_method_order**：类里 public/dunder 方法必须排在 private 方法之前；
2. **check_translatable_labels**：`BrowseFolder` / `RecommendationFolder` 不允许硬编码 `name`，
   必须有 `translation_key` + `strings.json` 条目；
3. **check_config_entries**：`ConfigEntry` 的 label/description/action_label、
   `ConfigValueOption` 的 title 不允许硬编码字符串；
4. **check_manifests / check_provider_icons**：manifest 必填字段、domain 与目录一致、
   依赖列表格式、icon 尺寸预算（svg ≤ 5 KB / png ≤ 20 KB）。

另外 `tests/providers/bilibili/test_locale.py` 会验证 `strings.json` 覆盖所有
config entry / 选项 / setup flow 条目 / browse+recommendation 文件夹，
并且 `manifest.description` 与 `strings.json` 的 `manifest.description` 一致。

## 缓存与图标

- view / 合集 meta / UP 名称 / 收藏夹列表的缓存已改用官方 `@use_cache`
  （`music_assistant.controllers.cache`），不再维护自建 dict 缓存；TTL 仍集中在
  `constants.py`（`VIEW_CACHE_TTL`、`COLLECTION_META_TTL`、`UP_PROFILE_TTL`、
  `FAVORITES_CACHE_TTL`）。
- 单元测试通过 `tests/providers/bilibili/fakes.py` 的 `FakeCache` 复刻
  `mass.cache` 的读写行为（真实控制器会 JSON 往返，`FakeCache` 也一样），
  并有“失败的请求不写入缓存、下次重试”的回归用例。
- 图标：`icon.svg`（品牌色）供默认/深色主题使用，`icon_monochrome.svg`
  （白色单色）供需要单色图标的 UI 使用；前端在浅色主题下会自动 `invert(1)`，
  两者都在 5 KB 预算内。

本地跑法：

```bash
ruff check provider tests
ruff format --check provider tests
python -m mypy
codespell provider tests scripts docs README.md README.zh-CN.md
python scripts/check_provider_conventions.py
python -m pytest -q
```

CI（`.github/workflows/ci.yml`）在 `ghcr.io/music-assistant/server:2.10.11` 里执行同样的命令；
`.pre-commit-config.yaml` 提供本地 pre-commit 钩子。

## 测试布局

官方 `scripts/check_test_layout.py` 要求 provider 测试必须放在按 provider 命名的目录里：

```text
tests/providers/bilibili/
├── __init__.py
├── test_catalog.py
├── test_client.py
├── test_collections.py
├── test_error_mapping.py
├── test_locale.py
├── test_parsers.py
├── test_provider.py
├── test_setup_flow.py
├── test_stream_selection.py
└── test_wbi.py
```

`tests/integration/` 只保留需要真实网络、默认 skip 的冒烟测试（`RUN_LIVE=1` 才执行）。

## 合并到官方仓库时仍要做的

1. provider 目录移动到 `music_assistant/providers/bilibili/`，测试移动到 `tests/providers/bilibili/`；
2. `strings.json` 交给官方 Lokalise 流程（本仓库已是英文源 + translation key）；
3. `manifest.documentation` 指向 `https://music-assistant.io/music-providers/bilibili`（需另提 docs PR）；
4. 删除本仓库的 `docker/`、`.github/workflows/`、`scripts/e2e_*.py`（官方统一镜像与 CI）。
