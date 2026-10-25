# tests agent notes

## 摘要

只新增了以下 3 个测试文件，未修改任何已有文件（未改 provider、docs、fixtures 或别人的测试）：

- `tests/unit/test_stream_selection.py`
- `tests/unit/test_error_mapping.py`
- `tests/integration/test_live_smoke.py`

测试全部离线：DASH payload 来自 `tests/fixtures/playurl.json`，HTTP 一律用 fake session。
`tests/fixtures/` 未新增文件：FLAC 候选按任务要求在测试内构造为 `dash.flac = {"audio": {...}}`。

### test_stream_selection.py

- 用 `FakeClient.playurl` 返回 `tests/fixtures/playurl.json` 的 `data`；
  `NoNetworkSession` 会在任何 HTTP 探测时直接失败，所有用例统一 `verify_stream_url=False`。
- 断言：
  - 默认 `auto` 选中 `30280`（fixture 中最高 rank 的 AAC）；
  - `quality=aac64` 选中 `30216`；
  - 动态加入 `30251` FLAC 候选后，`auto` 选中 `30251`；
  - 同一带 FLAC 的 payload 下，`quality=aac192` 选中 `30280`，不会选 FLAC；
  - `StreamDetails` 契约：`StreamType.HTTP`、`can_seek=True`、`allow_seek=True`、
    `extra_input_args == constants.FFMPEG_HEADERS`（含 `Referer:` / `User-Agent:`）、
    `300 <= expiration <= 7200`（fixture deadline 很远，实际被 clamp 到 7200）、
    `duration=213`、`provider`/`item_id` 正确；
  - fake client 收到 `playurl(bvid="BV1fixture", cid=1001)`，不会调用 view。

### test_error_mapping.py

- provider 映射表（直接调用 `BilibiliProvider._translate_error`）：
  - `-352` / `-412` -> `ResourceTemporarilyUnavailable`，backoff 分别为 30 / 60；
  - `-404` / `62002` / `62004` -> `MediaNotFoundError`；
  - `87007` / `87008` -> `UnplayableMediaError`；
  - 未知 code -> `ResourceTemporarilyUnavailable`，backoff 5。
- 通过 `provider.search()` 验证了任务要求的 5 个 code（`-352/-412/-404/62002/87007`）的
  wiring，以及 `-352` 的 backoff 透传。
- 网络/HTTP 翻译（真实 `BilibiliClient` + fake aiohttp session，完全不联网）：
  - `aiohttp.ClientError` / `TimeoutError` -> `ResourceTemporarilyUnavailable`，backoff 5；
  - HTTP `429` / `5xx` -> `ResourceTemporarilyUnavailable`，backoff 30；
  - HTTP `412` -> `BiliApiError(-412)` -> provider 翻译为 `ResourceTemporarilyUnavailable`，backoff 60。

### test_live_smoke.py

- `pytestmark = [pytest.mark.live, pytest.mark.skipif(RUN_LIVE != "1")]`，默认跳过。
- 流程：真实 `search_video("晴天")` -> `view(bvid)` 取 `pages[0].cid` ->
  `playurl(bvid, cid)` -> 按 `AUDIO_ID_RANK` 选最佳 `dash.audio` URL ->
  发 `Range: bytes=0-1023` 请求（带 Referer/Origin/User-Agent），断言 HTTP 200 或 206。
- 本轮未设置 `RUN_LIVE=1`，未访问外网。单独运行方式：

  ```bash
  docker exec -e RUN_LIVE=1 -w /workspace bilibili-ma-dev \
    /app/venv/bin/python -m pytest tests/integration/test_live_smoke.py -q
  ```

## 测试结果

容器：Python 3.14.7。

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/test_stream_selection.py tests/unit/test_error_mapping.py tests/integration/test_live_smoke.py -q` | `28 passed, 1 skipped` |
| `pytest -q`（全量） | `174 passed, 1 skipped` |
| `ruff check tests` | `All checks passed` |
| `ruff check <三个新文件>` | `All checks passed` |

全量基线为本 agent 开始前的 `146 passed`；新增 28 个通过用例 + 1 个默认跳过的 live 用例。

## 与契约的偏差

- 未发现 provider 行为与 `docs/contracts.md` 的偏差；流选择与错误映射的断言均按契约通过。
- 一个模型行为说明（不是 provider bug）：`AudioFormat.__post_init__` 会把大于 10000 的
  `bit_rate` 从 bps 归一化为 kbps，因此测试断言 `203` / `1411`，而非 `203786` / `1411200`。
  provider 传 `bandwidth`（bps）是正确的。
- live 测试未执行，因此真实 Bilibili API / CDN 的可达性未在本轮验证。

## 交给 lead 的问题

1. `ruff check provider tests` 目前失败，共 5 个错误，全部在 provider 既有文件中
   （不属于 tests 负责范围，未修改）。均为 `RUF001`（中文字符串里的全角标点）：
   - `provider/bilibili/compat.py:19:70` RUF001 全角逗号
   - `provider/bilibili/compat.py:21:40` RUF001 全角左括号
   - `provider/bilibili/compat.py:21:71` RUF001 全角右括号
   - `provider/bilibili/constants.py:114:8` RUF001 全角左括号
   - `provider/bilibili/constants.py:114:95` RUF001 全角右括号
   建议 lead 在 `pyproject.toml` 为中文文案加 per-file ignore（如
   `"provider/bilibili/*.py" = ["RUF001"]`），或在对应行加 `# noqa: RUF001`。
   `tests/` 本身 lint 干净。
2. 未发现 provider 代码 bug（流选择、错误映射均符合契约）。
3. 未执行任何 `git add` / `git commit`。
