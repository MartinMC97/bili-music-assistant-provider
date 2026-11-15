# 对齐 music-assistant/server 的检查（v1）

目标：让本仓库的代码风格与测试达到 **music-assistant/server 2.10.11** 的合并标准，
方便后续以 provider 形式并入官方仓库（`music_assistant/providers/bilibili/` + `tests/providers/bilibili/`）。

## 工具与版本（与官方 `pyproject.toml` 一致）

| 工具 | 版本 | 配置位置 | 检查范围 |
|---|---|---|---|
| ruff | `0.15.22` | `[tool.ruff]` / `[tool.ruff.lint]` | `provider tests`，`select = ["ALL"]` + `extend-select = ["D213"]`、line-length 100、py314、docstring pep257、mccabe 25、pylint 限制、isort first-party=`bilibili` |
| ruff format | 同上 | `[tool.ruff.format]` | 强制格式化（line-ending lf） |
| mypy | `2.3.0` | `[tool.mypy]` | strict（`disallow_untyped_defs` 等），`files = ["provider/bilibili", "tests"]` |
| codespell | `2.4.3` | `[tool.codespell]` | `provider tests scripts docs README.md` |
| pytest | 9.x | `[tool.pytest.ini_options]` | `asyncio_mode=auto`、`timeout=120`，默认只跑离线测试（`RUN_LIVE=1` 才跑真实 API） |

跑法：

```bash
ruff check provider tests
ruff format --check provider tests
python -m mypy
codespell provider tests scripts docs README.md
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
├── test_parsers.py
├── test_provider.py
├── test_setup_flow.py
├── test_stream_selection.py
└── test_wbi.py
```

`tests/integration/` 只保留需要真实网络、默认 skip 的冒烟测试（`RUN_LIVE=1` 才执行）。

## 仍未覆盖的官方仓库脚本（合并前需要保证通过）

官方仓库在 pre-commit 中还有一批仓库自带脚本，本仓库没有复制，但代码已经按它们的要求改过：

- `check_manifests`：manifest 必填字段、domain 与目录一致（本仓库 provider 目录不同，合并时目录会重排）
- `check_config_entries`：ConfigEntry 文案不允许硬编码，必须走 `strings.json`
- `check_translatable_labels`：browse / recommendation 的 label 必须用 `translation_key`
- `check_method_order`：类里 public/dunder 方法必须排在 private 方法之前
- `check_blocking_io` / `check_datetime_helpers`：async 代码不允许阻塞调用、不允许直接 `datetime.now()`
- `check_provider_icons`：icon svg ≤ 5 KB、png ≤ 20 KB
- `gen_requirements_all`：provider 依赖写在 `manifest.json` 的 `requirements`（本仓库已写入 `segno==1.6.6`）
- `build_translations`：`strings.json` 以英文为源，翻译由 Lokalise 管理（下一步工作）

## 合并到官方仓库时还要做的

1. provider 目录移动到 `music_assistant/providers/bilibili/`，测试移动到 `tests/providers/bilibili/`；
2. `strings.json` 改为英文源 + `translation_key`（配置项、browse/recommendation 文件夹）；
3. 类内方法顺序调整为 public → private；
4. 删除本仓库的 Dockerfile / GHCR workflow / e2e 脚本（官方统一镜像与 CI）；
5. manifest 的 `documentation` 指向 `https://music-assistant.io/music-providers/bilibili`（需另提 docs PR）。
