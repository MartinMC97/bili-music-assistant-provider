# Bilibili MA Provider — Code Review

> **修复状态（2026-10-25 23:57）**：本报告是修复前快照。P0（setup 非 async）与 P1-1～P1-5 已修复并回归；P2-1/2/3/4/5/6/7/8/9/10/19/20/21/22 已处理或部分处理。剩余 P2 与真实账号验证项见 `docs/devlog.md` 待办。

审查人：reviewer  
审查对象：`provider/bilibili/` 全部模块 + `tests/`  
目标环境：Music Assistant **2.10.8**（兼容 2.10.7），`music-assistant-models==1.1.205`，Python 3.14  
审查方式：逐文件阅读 + 与容器内 MA 2.10.8 源码/模型签名对照 + 离线执行测试/lint + 用 fake 对象做行为复现。未修改任何代码。

## 0. 结论

**当前状态：不可合并。存在 1 个 P0（provider 完全无法被 MA 加载）和 5 个 P1。**

| 级别 | 数量 | 摘要 |
|---|---|---|
| P0 | 1 | `setup()` 是同步函数，MA 2.10.x 会 `await` 它，导致 provider 加载直接 `TypeError` |
| P1 | 5 | setup flow 漏捕 `ResourceTemporarilyUnavailable`；启动时 `-352/-412` 会误降级丢失登录；重新配置选“匿名”无法登出；quality 上限被突破；`ruff check` 失败 |
| P2 | 若干 | av id、播放元数据、探测异常、收藏夹 position/has_more、缓存、日志硬化、测试缺口等 |

---

## 1. 测试与 lint 结果（reviewer 实跑）

```text
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python -m pytest -q
# 174 passed, 1 skipped in 4.92s

docker exec -w /workspace bilibili-ma-dev /app/venv/bin/ruff check provider tests
# Found 5 errors:
#   provider/bilibili/compat.py:19:70   RUF001 全角逗号
#   provider/bilibili/compat.py:21:40   RUF001 全角左括号
#   provider/bilibili/compat.py:21:71   RUF001 全角右括号
#   provider/bilibili/constants.py:114:8   RUF001 全角左括号
#   provider/bilibili/constants.py:114:95  RUF001 全角右括号
# [exit code: 1]
```

测试全绿，但 P0 没有被测出来：`tests/unit/test_provider.py:308-316` 直接同步调用 `setup()`，与 MA 的真实加载路径不一致（见 P0）。

补充：`ruff format --check provider tests` 报告 8 个文件待格式化（`auth.py`、`catalog.py`、`constants.py`、`parsers.py`、`setup_flow.py`、`tests/unit/test_catalog.py`、`test_error_mapping.py`、`test_setup_flow.py`）。如果 CI 含 format 门禁，这也会红；不属于本次要求的 `ruff check`，列作 P2。

`tests/integration/test_live_smoke.py` 默认 `skip`，本轮没有真实 API/CDN 验证。

---

## 2. P0 — provider 无法加载

### P0-1 `setup()` 必须是 `async def`

**位置**：`provider/bilibili/__init__.py:95-99`

```python
def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Initialize the provider."""
    return BilibiliProvider(mass, manifest, config, SUPPORTED_FEATURES)
```

**证据**：

- MA 2.10.8 加载 provider 时执行 `provider = await prov_mod.setup(self, prov_manifest, conf)`：
  - 容器内 `/app/venv/lib/python3.14/site-packages/music_assistant/mass.py:1356`
  - 本地参考源码 `.refs/ma-2.10.8/music_assistant/mass.py:1356` 与 `.refs/ma-2.10.7/music_assistant/mass.py:1356` 完全一致。
- `BilibiliProvider` 不是 awaitable（`hasattr(BilibiliProvider, "__await__") == False`）。
- 实测：

```text
await setup(FakeMass(), MANIFEST, config)
TypeError: 'BilibiliProvider' object can't be awaited
```

- git 骨架 `HEAD:provider/bilibili/__init__.py` 本来就是 `async def setup(...)`；本次重写时把 `async` 去掉了。
- 现有测试 `tests/unit/test_provider.py:316` 是 `provider = setup(...)`，所以测试绿、真实加载必炸。
- 容器日志里 15:12 UTC 的 “Loaded music provider Bilibili” 是骨架 async 版本时的加载记录，不能作为当前代码可加载的证据。

**修复建议**：

1. `provider/bilibili/__init__.py:95` 改为 `async def setup(...)`。
2. `tests/unit/test_provider.py:308-320` 改为 `provider = await setup(FakeMass(), MANIFEST, config)`。
3. 增加一条回归测试：`assert inspect.iscoroutinefunction(bilibili.setup)`（或真实 `await setup(...)`）。
4. 如果希望额外兼容 2.11 dev（非目标），可加 keyword-only `auto_setup: bool = False`；2.11 会先 `inspect.signature` 判断，不加也不会阻塞 2.10.8。

---

## 3. P1

### P1-1 setup flow 没有捕获真实 client 的网络异常类型

**位置**：

- `provider/bilibili/setup_flow.py:216-217`（QR generate/poll 的异常翻译）
- `provider/bilibili/setup_flow.py:275-280`（登录成功后取 buvid 的容错）

`BilibiliClient` 对网络错误、超时、HTTP 5xx/429 一律抛 `ResourceTemporarilyUnavailable`（`client.py:219-237`），而不是 `aiohttp.ClientError`。但 setup flow 只捕获：

```python
except (TimeoutError, OSError, aiohttp.ClientError) as err:   # setup_flow.py:216
except (BiliApiError, TimeoutError, OSError, aiohttp.ClientError) as err:  # setup_flow.py:277
```

后果：

1. `qrcode_generate()` / `qrcode_poll()` 的网络失败会以原始 `ResourceTemporarilyUnavailable` 冒泡，而不是契约要求的 `SetupFlowError`；UI 看到的是未预期异常。
2. 扫码**已经成功**后，`spi()` 只要网络失败，`_credential_from_cookies` 不会兜底返回 credential，而是把整个 setup flow 打成失败，已拿到的 SESSDATA 被丢弃。

**已复现**：注入 `spi_error=ResourceTemporarilyUnavailable("net")` 后，`run_setup()` 抛 `ResourceTemporarilyUnavailable`，`session.finished == []`。

现有 `tests/unit/test_setup_flow.py:358-380` 只注入了 `BiliApiError`，所以测试绿；`test_qr_network_error_becomes_setup_flow_error` 注入的是原始 `aiohttp.ClientConnectionError`，也不是真实 client 的行为。

**修复建议**：

- 两处都捕获 `MusicAssistantError`（或显式加 `ResourceTemporarilyUnavailable`）；`spi()` 失败继续 `return credential`。
- 补测试：用真实 `BilibiliClient` + fake session（或注入 `ResourceTemporarilyUnavailable`）覆盖 generate/poll/spi 三条路径。
- 注意日志仍只打 endpoint，不要打 URL/Cookie（现状没问题）。

### P1-2 启动时 `-352/-412` 会被误判为“登录失效”，整轮丢失收藏夹

**位置**：`provider/bilibili/__init__.py:381-391`

```python
except BiliApiError as err:
    self.logger.warning(...)
    self._degrade_to_anonymous(client)
    return
```

`BiliApiError` 包含的不只是鉴权失败：`-352`（风控）、`-412`（请求被拒）、HTTP 4xx/5xx、无效 JSON 都会走到这里。`_degrade_to_anonymous` 会清掉内存里的 SESSDATA/DedeUserID，于是本次 provider 运行期间收藏夹全部不可用，直到重启；而 setup_data 里其实仍有有效凭据。

**已复现**：nav 抛 `BiliApiError(-352)` → `has_login == False`，`_mid == 0`。

**修复建议**：

- 只有明确的鉴权失败（`-101`，必要时 `-111`）或 `nav.isLogin is False` 才降级；
- `-352/-412` 及其他未知 code 按“暂时无法校验”处理：warning + 保留 credential，不 `_degrade_to_anonymous`；
- 加参数化测试：`-101` 降级、`-352` 保留、`-412` 保留、网络错误保留（网络错误已有测试）。

### P1-3 重新配置时选“匿名”无法登出

**位置**：`provider/bilibili/setup_flow.py:146-148`

```python
if method == METHOD_ANONYMOUS:
    await session.finish({})
    return
```

首次 setup 时 `{}` 会创建空 setup_data；但 `reconfigure_provider` 走的是 `_finish_provider_reconfigure`，MA 的实现是把新值 merge 进旧 setup_data：

```python
snapshot = dict(raw_conf.get("setup_data") or {})
self.set(f"{conf_key}/setup_data", {**snapshot, **self._encrypt_values(values)})
```

见 `music_assistant/controllers/config/flows.py:463-474`。因此旧的 `credential_json` 不会被删除，用户重新配置选择“Continue anonymously”后实际上仍是登录态，无法登出。

**修复建议**：

- 匿名分支改为 `await session.finish({constants.CONF_CREDENTIAL_JSON: ""})`；`_load_credential` 对空字符串返回空 credential（当前 `BilibiliCredential.from_json("")` 返回空，已满足）。
- 补一条 setup flow 测试，断言 anonymous 分支的 `finish` 值包含清空用的 key，并在注释里说明 reconfigure 的 merge 语义。

### P1-4 `quality` 配置上限会被静默突破

**位置**：`provider/bilibili/__init__.py:799-808`

```python
eligible = [stream for stream in candidates if _audio_rank(stream) <= max_rank]
if not eligible:
    ...
    eligible = candidates
```

当用户选 `aac64`（上限 rank 10），而视频只有 FLAC 时，代码会回退到 FLAC。**已复现**：`aac64` + 仅 FLAC → `path` 为 FLAC URL。契约要求“按 `quality` 配置上限与 `AUDIO_ID_RANK` 排序”，这与“上限”语义直接冲突；对移动网络/流量敏感用户是行为偏差。

**修复建议**：

- 严格尊重上限：`if not eligible: raise UnplayableMediaError(...)`，或
- 保留兜底但把配置文案改成“偏好”而非“上限”，并明确日志/warning；无论选哪种，需要与契约 owner 对齐并加测试（现有测试只覆盖“低档存在时不会选高档”，没有覆盖“只有高档时”）。

### P1-5 `ruff check provider tests` 失败（5 个 RUF001）

**位置**：

- `provider/bilibili/compat.py:19:70`（全角逗号 `，`）
- `provider/bilibili/compat.py:21:40`、`21:71`（全角括号）
- `provider/bilibili/constants.py:114:8`、`114:95`（全角括号，正则里是刻意匹配 B 站标题）

**修复建议**：

- 中文提示文案（compat.py）改成 ASCII 标点；或
- 对应行加 `# noqa: RUF001`（`parsers.py:35-40` 已有同样处理），或
- 在 `pyproject.toml` 为这两个文件加 per-file ignore。
- 无论哪种，当前 lint 门禁是红的，属于合并前必须清掉的 P1。

---

## 4. P2 — 播放路径

### P2-1 `av<aid>` id 无法解析（`get_track` / 无 cid 的 `get_stream_details`）

**位置**：

- `client.py:350-356`：`view()` 只接受 `bvid`，请求固定 `{"bvid": bvid}`。
- `__init__.py:708-712`：`get_track("av123...")` 会把 `av123...` 当 bvid 传给 `view()`。
- `__init__.py:774-779`：`get_stream_details("av123")` 无 cid 时直接抛 `UnplayableMediaError`，不会用 aid 去取 pages[0].cid。

契约 §4.3（implementation-plan）明确允许音乐中心 fallback 产生 `av123456#789012`；`parse_music_center_item` 也确实会生成这种 id（`parsers.py:464-467`）。带 cid 的播放路径能工作（测试 `test_stream_handles_avid_and_single_dict_flac` 覆盖），但无 cid 时不可播、`get_track` 永远解析不了。

**建议**：`BilibiliClient.view` 增加 `aid` 可选参数（`/x/web-interface/view?aid=`），`_first_cid`/`get_track` 按 `av` 走 aid；补 `av` 无 cid 的测试。

### P2-2 `_request_probe` 只捕获网络异常，坏 URL 会打断播放

**位置**：`__init__.py:852-869`

`self.mass.http_session.get(url, ...)` 对格式非法的 URL 可能抛 `ValueError`/`yarl.InvalidUrl`（`InvalidUrl` 是 `ValueError` 子类），当前 except 只包含 `TimeoutError, aiohttp.ClientError, OSError`，异常会冒泡到 `get_stream_details`，导致本可回退的播放失败。

**建议**：`except (TimeoutError, OSError, ValueError, TypeError, aiohttp.ClientError): return False`；`asyncio.CancelledError` 不要吞。加测试：`RecordingHTTPSession.get` 抛 `ValueError` 时仍返回首选 URL。

### P2-3 CDN 负健康缓存 10 分钟，瞬时失败会“毒化”整个 host

**位置**：`__init__.py:840-850`

探测失败会写 `(False, now + 600)`，且 key 只有 host。之后同 host 的所有流都直接跳过探测；若 3 个 URL 都失败，播放永远返回 primary，不再尝试 backup。

**建议**：不缓存失败（或失败 TTL 降到 30-60s），成功再缓存 10 分钟；或按 URL 缓存并保留 host 级快速判断。

### P2-4 AudioFormat 元数据硬编码，Dolby 声道数可能触发错误 downmix

**位置**：`__init__.py:278-299`

- FLAC 固定 96kHz/24bit；
- Dolby 固定 48kHz/**6ch**；
- AAC 固定 44.1kHz，`ContentType.M4A`，`codec_type` 未设置。

MA 的 ffmpeg helper 只在解析到输入流后更新 `sample_rate/bit_depth/bit_rate`，**不会更新 `channels`**（`helpers/ffmpeg.py:315-333`）；而 `_get_channel_conform_filter(input_channels, output_channels)` 会按声明的 channels 决定是否插入 5.1→2.0 的 pan 滤波（`helpers/ffmpeg.py:664, 787-810`）。如果 B 站某个 Dolby 流实际是 2.0，声明 6ch 会错误改变音频。AAC 的 44.1k 也与 implementation-plan §1.1 的“48kHz”不一致。

**建议**：优先设置 `codec_type`（AAC/FLAC/EAC3）和 `content_type`（AAC 用 `ContentType.MP4`，与 plan §4.4 一致），未知的 `channels/sample_rate/bit_depth` 用模型默认值而不是猜 6ch；或从 `mimeType`/已知 B 站规格推导。

### P2-5 `StreamDetails` 缺少 plan 约定的 `data`/显式 `media_type`

**位置**：`__init__.py:816-827`

plan §4.4 建议 `data={"host": ..., "backup": [...]}`（不序列化、便于调试）并显式 `media_type=MediaType.TRACK`。当前只依赖默认值，功能不受影响，但与计划不一致，后续排障少一个入口。

### P2-6 没有覆盖 `max_concurrent_streams = 3`

**位置**：类 `BilibiliProvider`（`__init__.py:308` 起）

plan §4.4 建议覆盖为 3（保守并发）；当前沿用 `MusicProvider` 的默认值（streaming provider 为 `DEFAULT_MAX_CONCURRENT_STREAMS`，2.10.8 中为 5）。不算契约硬性要求，但属于计划偏差，建议明确采用 3 或删掉计划条目。

### P2-7 `parse_view` 单 P 时长回退不完整

**位置**：`parsers.py:342-345`

```python
duration = first_page.get("duration") if multi_part else data.get("duration")
if duration is None:
    duration = data.get("duration")
```

单 P 且顶层 `duration` 缺失、`pages[0].duration` 存在时，第一遍取 `None`，第二遍又取顶层 `None`，最终 duration=0。建议直接 `duration = data.get("duration") or first_page.get("duration")`，并补测试。

### P2-8 `_response_cookies` 提前返回，可能漏 Set-Cookie

**位置**：`client.py:94-123`

如果 `response.cookies` 非空但只包含部分 cookie，函数会直接 `return cookies`，不再解析 `Set-Cookie`。二维码登录依赖 SESSDATA/DedeUserID，建议总是把 `response.cookies` 与 `Set-Cookie` 合并（后者只补缺失 key），避免部分解析导致登录失败。

### P2-9 `_view` 缓存上限并非硬上限

**位置**：`__init__.py:581-585`

达到 256 后只过滤过期项；如果 256 条都未过期，过滤后仍 256，再插入变 257，下一轮继续增长。建议插入前按最早过期/最久未用淘汰一条，或直接用 `OrderedDict`。

### P2-10 `spi()` 的 `_data(payload) or payload` 语义混乱

**位置**：`client.py:321`

`data` 为空 dict 时返回外层 payload（含 code），随后 `data.get("b_3")` 永远空；不会崩，但建议直接 `data = _data(payload)`，让调用方拿到稳定的空值。

---

## 5. P2 — 收藏夹 / 分页

### P2-11 `position` 跨页不连续

**位置**：`catalog.py:219-230`

`position = page * 20 + 1` 起，被跳过的失效项不占号。于是第 1 页若 3 条中 1 条失效，position 是 `[1,2]`；第 2 页从 21 开始，出现 3..20 的空洞。契约明确写“`position` 跨页从 1 连续递增”；implementation-plan 也说“否则 MA 排序会乱”。现有测试 `test_get_favorite_tracks_page_offset` 断言 `[21,22]`，正好固化了这个偏差。

**建议**：让分页逻辑维护全局有效计数（例如 `get_favorite_tracks` 先统计前 `page` 页的有效条数，或 client 层提供 `iter_favorite_items`），或将契约改成“页内连续、跨页按页偏移”并由 lead 确认。MA 2.10.8 当前播放列表迭代并不读取 `track.position`（只按返回顺序），所以这是契约/未来风险，不是当前立即播放故障。

### P2-12 `get_favorite_tracks` 忽略 `has_more`，全失效页会让 MA 提前停止

**位置**：`catalog.py:216-231`

函数只取请求页；MA `PlaylistsController.tracks()` 以“返回空列表”作为结束条件（`controllers/music/media/playlists.py:146-160`）。如果某一页 20 条全部失效/被跳过，返回 `[]`，MA 会停止翻页，后面的歌全部丢失。同时函数没有读 `payload["has_more"]`，与契约“`has_more` 继续”不一致。

**建议**：`get_favorite_tracks` 在结果为空且 `has_more` 为真时继续取下一页，或让 provider 的 `get_playlist_tracks` 在空页时向后探测；补“第一页全失效、第二页有效”的测试。

### P2-13 `fav:0` 被拒绝（需确认真实默认收藏夹 id）

**位置**：`catalog.py:85-86`

`_parse_playlist_id` 拒绝 `media_id <= 0`。如果某些账号/接口把“默认收藏夹”返回为 `id=0`，该收藏夹会被跳过（`parse_fav_folder` 仍会生成 `fav:0`，但 tracks 永远 MediaNotFound）。现有 fixture 只有正数 id，未验证。建议用真实账号确认默认夹 id；若可能为 0，允许 0 并只拒绝负数。

### P2-14 `get_favorite_playlist` 每次全量扫描收藏夹

**位置**：`catalog.py:190-198`

每次 `get_playlist` / playlist 同步都会先 `fav_folders`，再把所有订阅夹页翻一遍，只为找 id。收藏夹多时是 O(页数) 次请求。建议加 300s 缓存（plan §5.5）或先按 `fav:<id>` 直接查 `/x/v3/fav/folder/info`。

### P2-15 `MAX_COLLECTED_PAGES=50` 静默截断

**位置**：`catalog.py:174-187`

超过 50 页订阅夹直接停止，没有 warning。建议至少 `logger.warning` 一次，便于用户知道收藏夹列表不完整。

### P2-16 音乐中心 `cid: "0"` 被当成有效 cid

**位置**：`parsers.py:445-467`

`tests/fixtures/new_music.json` 的 `cid` 是字符串 `"0"`，`parse_music_center_item` 会生成 `BV...#0`。provider 的 `_split_track_id` 把 0 当“无 cid”（`__init__.py:547`），所以播放能回退 `view()`；但同一个视频在推荐里是 `BV#0`、在搜索里是 `BV`，`UniqueList`/library 匹配会当成两个 item。

**建议**：parser 层把 `cid in ("", "0", 0)` 视为无 cid；补 `new_music.json` 的断言。

### P2-17 综合榜顶层 bvid 失效时不回退 `related_archive`

**位置**：`parsers.py:439-454`

只有顶层缺 `bvid` 才使用 `related_archive.bvid`；如果顶层 `bvid` 存在但已失效（契约/计划提到综合榜可能出现），不会替换。建议在 parser 或 catalog 层做一次有效性判断/回退，至少记录 debug。

### P2-18 收藏夹 owner 对自建夹为空

**位置**：`parsers.py:425`（`owner=str(owner_name or "")`）、`catalog.py:90-100`

`created/list-all` 不返回用户名，catalog 签名也拿不到 provider 的 `_user_name`，所以自建收藏夹 owner 为空；plan §5.8 希望显示登录用户名。建议 provider 层把 `nav.uname` 传给 catalog，或接受空 owner 并更新计划。

---

## 6. P2 — 安全 / 配置 / 分发

### P2-19 `BilibiliCredential` 的 dataclass `repr` 会包含 SESSDATA

**位置**：`auth.py:21-30`

`@dataclass(frozen=True, slots=True)` 默认 `repr` 会打印 `sessdata`、`bili_jct`。当前代码没有 `logger.debug("%s", credential)`，但这是很容易被后续改动踩中的泄漏点。

**建议**：给敏感字段加 `field(repr=False)`，或自定义 `__repr__` 只显示 `has_login/mid`。补一条“repr 不含 sessdata”的测试。

### P2-20 `BilibiliCredential.from_json` 的 `saved_at` 可能抛 ValueError

**位置**：`auth.py:92`

`int(data.get("saved_at") or 0)` 对 `"abc"`、list 等会抛 `ValueError/TypeError`；`from_json` 的 try 只包住 `json.loads`，没有包住字段转换。损坏的 setup_data 会让 `handle_async_init` 直接失败。建议 `try/except (TypeError, ValueError)` 回退 0。

### P2-21 `manifest.requirements=[]` + 仓库没有 Dockerfile：扫码默认不可用

**位置**：`provider/bilibili/manifest.json`、`docker/`

manifest 依赖为空符合 plan §2.4（避免可选依赖拖垮加载），`setup_flow.py` 也做了 segno 缺失降级，这部分正确。但仓库 `docker/` 只有 compose，没有 plan §2.4 说的“Dockerfile 预装 segno==1.6.6”；stock 镜像里没有 segno（当前 dev 容器有，是环境预装的），用户扫码会一直回退到手动表单。建议补 Dockerfile / README 安装说明，或接受“扫码需要手动装 segno”并在 UI 文案里写清。

### P2-22 `segno` 渲染异常未翻译

**位置**：`setup_flow.py:222-232`

`segno.make(...).save(...)` 对非法内容/内存不足可能抛 `segno` 自己的异常，当前没有被捕获成 `SetupFlowError`。建议包一层 `try/except Exception`（不要吞 `CancelledError`）→ `SetupFlowError`。

### P2-23 已登录凭据中途失效时，收藏夹不是“同匿名”，而是反复临时错误

**位置**：`__init__.py:874-883`、`catalog.py:164-186`

如果启动时 nav 校验成功、之后 SESSDATA 过期，`fav_folders` 会抛 `BiliApiError(-101)`，provider 的 `_translate_error` 会转成 `ResourceTemporarilyUnavailable(backoff=5)`，MA 会反复重试并显示错误，而不是像匿名一样返回空。plan §5.8 要求“登录但凭据失效：同匿名，warning 一次”。建议在 `get_library_playlists`/`get_playlist_tracks` 捕获 `BiliApiError` 且 `code == -101` 时 warning + 空/空列表，并考虑标记 credential 失效。

### P2-24 日志安全现状：provider 层没有泄漏，但仍有环境级风险

我逐条检查了 `provider/bilibili` 的日志：

- `client.py:211` 只打 endpoint；
- `__init__.py` 的 warning 只打 `_describe_error`（BiliApiError 的 message/endpoint）和 `_host()`；
- `setup_flow.py` 只打 method/status/err，没有 Cookie/SESSDATA/完整 URL；
- `setup_data` 由 MA 加密落盘（`controllers/config/flows.py:707-712`），手动 SESSDATA 是 `SECURE_STRING`。

**残余风险**：

- `docker/compose.dev.yml` 设置了 `LOG_LEVEL: debug`；aiohttp/MA 的 HTTP trace 是否会在 DEBUG 下打印带 WBI 签名/搜索关键字的完整 URL，不在 provider 控制内。生产默认不要开 debug。
- `auth.BilibiliCredential` 的 repr 见 P2-19。
- 工作区存在未跟踪的 `artifacts/token.txt`（377 字节，本轮**没有读取内容**），且 `.gitignore` 只忽略 `artifacts/*.log`/`*.json`，没有忽略 `token.txt`。提交前请确认它不是真实 token；如果是，应删除或加入 `.gitignore`。

### P2-25 `get_stream_details` 对非 TRACK 抛 `MediaNotFoundError`

**位置**：`__init__.py:767-768`

语义上“媒体类型不支持”更像 `UnplayableMediaError`/`InvalidDataError`，`MediaNotFoundError` 可能让 MA 把 item 当不存在。契约未规定；建议与 lead 对齐，避免未来 provider 支持其他 media type 时误删条目。

---

## 7. 与契约 / implementation-plan 的偏差汇总

| # | 项目 | 实现 | 契约/计划 | 级别 |
|---|---|---|---|---|
| 1 | 模块级 `setup()` | 同步（`__init__.py:95`） | MA 2.10.x 必须 `await`（骨架也是 async） | **P0** |
| 2 | client 失败类型 | 网络/5xx/429 抛 `ResourceTemporarilyUnavailable`（`client.py:219-237`） | contracts §client：“失败统一抛 `BiliApiError`，provider 层翻译” | P2（但直接导致 P1-1） |
| 3 | 收藏夹 pagination | `get_favorite_tracks` 忽略 `has_more`，空页停止 | contracts §catalog：“`has_more` 继续” | P2 |
| 4 | 收藏夹 position | 页内连续、跨页按 `page*20` 偏移 | contracts §catalog：“跨页从 1 连续递增” | P2 |
| 5 | quality 上限 | 无低档候选时回退最高档 | contracts §`get_stream_details`：“按 quality 配置上限” | **P1** |
| 6 | 音乐中心 cid | `"0"` 生成 `#0` | plan §4.3 的 id 规则应无 cid | P2 |
| 7 | `StreamDetails` | 无 `data`、未显式 `media_type`；AudioFormat 用 M4A/硬编码参数 | plan §4.4 建议 MP4/FLAC + codec_type、`data`、`max_concurrent_streams=3` | P2 |
| 8 | 自建收藏夹 owner | 空 | plan §5.8 希望登录用户名 | P2 |
| 9 | 缓存 | 只有 provider 的 view 内存缓存；search/ranking/favorites 无 300-3600s 缓存 | plan §5.5 的缓存策略 | P2 |
| 10 | `get_artist` | 只查本次运行见过的名字，不联网 | plan §4.3 的“最小 Artist 实现”可接受，但重启后名字是通用 fallback | P2 |
| 11 | 搜索 `page_size` | `clamp(max(wanted,20),20,50)` | contracts 未规定，plan 允许；仅多取 | 记录 |
| 12 | `requirements` / segno | `[]`，延迟 import | plan §2.4 独立分发阶段正确，但缺 Dockerfile 预装 | P2 |

---

## 8. 测试覆盖评估

**做得好的部分**：

- `wbi.py` 有黄金向量；`parsers.py` 覆盖标题清洗、时长、失效项、音乐中心 fallback；
- `client.py` 覆盖签名 query 原样发送、WBI 缓存、API code/HTTP/网络错误；
- `provider.py` 覆盖 quality、backup 探测、expiration、header、get_track、init 凭据；
- `catalog.py` 覆盖收藏夹分页/去重/匿名/坏数据、推荐、browse；
- 所有测试离线，live 测试默认 skip，符合硬规则。

**关键缺口**（按风险排序）：

1. **没有真实 MA 加载测试**：`tests/unit/test_provider.py:308` 同步调用 `setup()`，掩盖 P0。至少加 `inspect.iscoroutinefunction(setup)` 或用 fake mass 做 `await setup(...)`。
2. **setup flow 只测 `BiliApiError`**（`tests/unit/test_setup_flow.py:318-380`），没有模拟真实 client 的 `ResourceTemporarilyUnavailable`；需要覆盖 generate/poll/spi 三处。
3. **`_verify_login` 没有 `-352/-412` 用例**；`test_init_nav_api_error_degrades_to_anonymous` 只测 `-101`，把“所有 BiliApiError 都降级”固化了。
4. **只测了“低档存在时 quality 生效”**，没测“只有高档时是否应失败/回退”（P1-4）。
5. **没有 av id 无 cid / `get_track(av...)`**。
6. **没有 reconfigure 场景**（匿名登出、setup_data merge）。
7. **没有全失效收藏页 / 跨页 position 连续** 的测试（现有 `[21,22]` 反而断言了偏差）。
8. **没有日志脱敏测试**（如 `caplog` 断言不含 SESSDATA/URL query）。
9. **没有 probe 遇到 `ValueError`/取消** 的测试。
10. **没有 `compat.check_compat()` 失败路径** 测试（monkeypatch `fields` 或直接构造）。
11. live smoke 未运行，真实 B 站 API、WBI、CDN、Dolby/FLAC 字段形态都未在本轮验证。

---

## 9. 通过项（已核对，未发现问题）

- **没有 2.11-only import**：`media_capabilities`、`MediaCatalogMixin`、`AudioStreamMixin`、`ConfigEntryType.FOLDER`、`helpers.mp3`、`ffmpeg_http_headers` 均未使用；`grep` 无结果。
- `music_assistant.constants` 的 `CONF_ENTRY_UNOFFICIAL_PROVIDER`、`PlaylistPlayableItem` 在 2.10.7/2.10.8 都存在；`MusicProvider.__init__`、`search/get_track/get_artist/get_stream_details/get_library_playlists/get_playlist/get_playlist_tracks/get_recommendations/get_recommendation_items/browse/unload/get_config_entries/handle_async_init` 签名与容器内 2.10.8 一致。
- `StreamDetails` 2.10.8 字段包含 `extra_input_args`、`can_seek`、`allow_seek`、`expiration`，构造调用合法；`FFMPEG_HEADERS` 会被复制成 list，避免 MA 后续 `+=` 修改常量。
- `setup_flow.py` 使用的 `SetupSession.form/progress_until/finish`、`SetupFlowError`、`StepExpiredError` 在 2.10.8 存在且参数匹配；`manifest.json` 可被 `ProviderManifest` 解析；MA 通过 `setup_flow.py` 文件存在自动设置 `has_setup_flow`，无需 manifest 字段。
- provider 内部全部相对 import；没有改 `docs/contracts.md` / `docs/implementation-plan.md`。
- `setup_data` 由 MA 的 `_finish_provider_setup/_finish_provider_reconfigure` 加密存储；`credential_json` 不会明文落盘。
- provider 代码日志只输出 endpoint/host/API code，没有发现完整签名 URL、Cookie、SESSDATA 泄漏。
- `pytest -q` 全绿（174 passed, 1 skipped）；`tests/` 的 ruff 干净（5 个 RUF001 都在 provider 的 lead 文件里）。

---

## 10. 建议修复顺序

1. **P0-1**：`async def setup` + 更新 `test_setup_returns_provider...` + 回归测试（阻塞一切）。
2. **P1-5**：清 lint（5 个 `# noqa: RUF001` 或 ASCII 文案）。
3. **P1-1/P1-2**：setup flow 捕获 MA 错误；`_verify_login` 只对鉴权失败降级；补测试。
4. **P1-3/P1-4**：reconfigure 匿名清凭据；quality 上限语义与契约对齐。
5. **P2 播放组**：av id、probe 异常、负缓存、AudioFormat/channels、StreamDetails.data、`max_concurrent_streams`。
6. **P2 收藏夹组**：position/has_more/空页、默认夹 id 验证、缓存。
7. **P2 安全/分发**：credential repr、`saved_at` 容错、segno 安装说明、`artifacts/token.txt` 处置。
8. **测试补强**：按第 8 节 11 个缺口逐项补离线用例；2.10.7/2.10.8 的加载 smoke 可在 CI 里用真实 MA import 一次（不联网）。

---

## 11. 备注

- 按角色要求只写了 `artifacts/review.md`；未修改任何 provider/test/docs 文件，未执行 `git add`/`git commit`。
- 未读取 `artifacts/token.txt` 的内容；只记录了它的存在和未被 `.gitignore` 覆盖的事实。
- 测试命令与结果见第 1 节；所有复现都使用 fake 对象，不联网。
