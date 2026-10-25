# agent-auth 交接笔记（setup_flow）

## 交付

- `provider/bilibili/setup_flow.py`：`run_setup(session)` 三种登录方式（qr / manual / anonymous），只依赖既有 `auth.py`、`client.py`、`constants.py`，无新依赖、无网络测试代码。
- `tests/unit/test_setup_flow.py`：12 个用例，全部 fake session / fake client（12 passed）。

## 行为摘要

1. `FORM step_id="choose"`：`method` 选项 `qr` / `manual` / `anonymous`，`default_value="qr"`，`expanded_options=True`。
2. `anonymous` → `session.finish({})`，不创建 client。
3. `manual` → `FORM step_id="manual"`：`sessdata`（`SECURE_STRING`，required）、`dedeuserid`（`STRING`，可选）→ `BilibiliCredential(sessdata=..., dedeuserid=..., saved_at=now)` → `finish({"credential_json": cred.to_json()})`。
4. `qr` → `BilibiliClient(mass=session.mass, credential=BilibiliCredential())` → `qrcode_generate()` → segno `make(url).save(buf, kind="png", scale=6, border=2)` → `data:image/png;base64,...` → `progress_until(_poll(...), step_id="scan_qr", image=..., expires_in=180)`。
   - `_poll`：`qrcode_poll`，2s 间隔；`0` 成功、`86038` 抛 `StepExpiredError`、`86101/86090` 继续（未知状态记 debug 后继续，由 180s deadline 兜底）。
   - 成功后 `BilibiliCredential.from_cookies(cookies)`（校验 `has_login`），再 `client.spi()` 取 `b_3/b_4`，用 `with_buvid()` 只补空缺 → `finish({"credential_json": ...})`。
   - client 始终在 `finally` 里 `close()`（当前实现是 no-op）。
5. `segno` 缺失：延迟 `import segno`（`_import_segno()`），失败回退到 `manual` 表单，`sessdata` 的 `description` 中提示 `pip install segno==1.6.6`；不抛异常。
6. 错误翻译：`BiliApiError` / `aiohttp.ClientError` / `TimeoutError` / `OSError` → `SetupFlowError`；`StepExpiredError` 原样向上抛（由 MA 引擎转 timed_out）。

## 测试

命令（容器内）：

```
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python -m pytest tests/unit/test_setup_flow.py -q
# 12 passed in 2.60s

docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python -m pytest tests/unit -q
# 74 passed in 4.94s（含 client/parsers/wbi 等其他模块）

docker exec -w /workspace bilibili-ma-dev /app/venv/bin/ruff check provider/bilibili/setup_flow.py tests/unit/test_setup_flow.py
# All checks passed!
```

覆盖点：anonymous、manual（含空白 SESSDATA 拒绝）、qr 成功（PNG 魔数、cookie/buvid、client 关闭、初始空凭据）、`86038` 过期、deadline 超时、segno 缺失回退（含真实 `ImportError` 模拟）、QR generate API 错误、轮询网络错误、spi 失败不丢弃成功登录。测试不联网、不含真实 Cookie（仅 `fake-*` 占位）。

## 与契约的偏差 / 自主决定（需 lead 知悉）

1. **二维码过期不自动重发**：契约只规定 `progress_until(..., expires_in=180)` 与成功路径；`86038` 和 deadline 超时都抛 `StepExpiredError` 结束流程（用户重跑 setup），没有 netease/qqmusic 那种 while 循环原地刷新。若希望“过期自动换新码”，需要给 `_run_qr_login` 加循环（契约未要求，未擅自加）。
2. **`spi()` 失败不终止登录**：扫码已成功时，若 `/x/frontend/finger/spi` 失败（`BiliApiError`/网络错误），记 warning 并继续 `finish`，只是没有 `buvid3/4`。理由：buvid 只是降低风控，不应让已完成的登录失败；契约只写“成功后调用 spi 补 buvid”。
3. **`sessdata` 用 `SECURE_STRING`**：契约未指定表单类型；手填 Cookie 属敏感值，按 MA 惯例用 secure string。`dedeuserid` 用 `STRING`。空白 `sessdata` 直接抛 `SetupFlowError`（引擎 required 校验之外的兜底）。
4. **UI 文案**：仓库暂无 `strings.json`（由 agent-provider 拥有），method 选项给了英文 `title`，manual 字段给了英文 `description`；segno 缺失提示写在 `sessdata.description` 前缀（符合契约“在 description 里给安装提示”）。后续如果加 translations，可改为 translation key。
5. **日志用模块级 `LOGGER`**：真实的 `SetupSession` 并没有 `logger` 属性（读了 2.10.8 源码确认），故不依赖 `session.logger`；日志不含 Cookie/异常里的凭据。
6. 未使用 `AbortFlow`：B 站二维码没有“用户拒绝”码，避免为凑 import 而引入未用符号。

## 边界

- 只改了上述两个文件；未改 `__init__.py`、`docs/*`、其他模块；未 `git add/commit`。
- `run_setup` 未做 `session.context.setup_data` 预填（契约未要求 reconfigure 预填）；如需 reconfigure 保留“上次 method”，可后续小改。
