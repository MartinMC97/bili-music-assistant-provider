# notes-provider — BilibiliProvider 与 provider 单测

负责人：provider
完成文件（仅这些）：

- `provider/bilibili/__init__.py`
- `tests/unit/test_provider.py`

## 摘要

### `provider/bilibili/__init__.py`

严格按 `docs/contracts.md` 的 `__init__.py` 段实现，未新增模块、未改他人文件。

- `SUPPORTED_FEATURES = {SEARCH, LIBRARY_PLAYLISTS, RECOMMENDATIONS, BROWSE}`；
  没有 `LYRICS` / `ARTIST_TRACKS`。
- `setup()` 返回 `BilibiliProvider(mass, manifest, config, SUPPORTED_FEATURES)`。
- `handle_async_init()`：
  - `check_compat()` 有 issue 时抛 `ProviderUnavailableError`。
  - 凭据优先级：`get_setup_value(CONF_CREDENTIAL_JSON)` → `get_config_value(CONF_SESSDATA_MANUAL)`。
  - 只有 SESSDATA 时先建仅含 SESSDATA 的 credential，`nav()` 成功后用 `merged()` 补
    `DedeUserID` / `mid` / `uname`。
  - 登录时 `nav()` 校验一次：`isLogin=false` 或 API 拒绝 → warning 并降级匿名（保留
    buvid，不阻塞加载）。
  - 匿名/降级后调用 `spi()` 补 `buvid3/4`；`spi()` 失败只 warning。
  - 记录 `self._user_name` / `self._mid`。
  - `self._view_cache: dict[str, tuple[float, dict]]`，TTL 用 `constants.VIEW_CACHE_TTL`
    （24h），时间基准 `time.monotonic()`。
- `get_config_entries()`：`quality`（options 来自 `constants.QUALITY_OPTIONS`，默认
  `auto`）、`min_duration`、`max_duration`、`search_limit`、`verify_stream_url`、
  `sessdata_manual`（`SECURE_STRING`）、`CONF_ENTRY_UNOFFICIAL_PROVIDER`。
- `search()`：
  - 只处理 `MediaType.TRACK`；空 query 直接空结果，不发请求。
  - `client.search_video(keyword, page=1, page_size=...)`；
  - `parsers.parse_search_item`；配置 min/max 时长过滤；
  - 以「clean title + 第一作者名」为 key 去重，重复项保留 `play` 最高者；
  - 按 `metadata.popularity`（play）降序；截断到 `min(limit, search_limit)`；
  - 返回 `SearchResults(tracks=...)`，同时把作者名字记入 `_artist_names`。
- `get_track()`：解析 `bvid#cid`；`view()` 走 24h 内存缓存；`parsers.parse_view`；
  有 cid 时用对应 `pages[].part` 作为 name（并同步该 P 的 duration）；最终 item_id 与
  provider_mapping 都保留调用方给的原始 `bvid#cid`。
- `get_artist()`：只查 `_artist_names` 内存 map，不联网；没有名字时返回
  `Bilibili UP 主 <mid>`；数字 id 附 `https://space.bilibili.com/<mid>`；
  未实现 `get_artist_tracks`。
- `get_stream_details()`：
  - 无 cid 时 `view()` 取第一个 page 的 cid；支持 `bvid` 和 `av<aid>`；
  - `playurl(bvid|avid, cid)`；候选来自 `dash.audio`（list）、`dash.dolby.audio`
    （list/dict）、`dash.flac.audio`（dict/list），统一成 `client.AudioStream`；
  - 按 `QUALITY_MAX_RANK` 过滤上限，再按 `AUDIO_ID_RANK` + bandwidth 选最高；
  - `verify_stream_url=true` 时对 baseUrl → backupUrl[0] → backupUrl[1]（最多 3 个）做
    `Range: bytes=0-1023`（1.5s）探测，状态 200/206 视为健康；全失败 warning（只打
    host）后仍返回首选；
  - `StreamDetails(HTTP, can_seek=True, allow_seek=True, extra_input_args=FFMPEG_HEADERS)`；
  - `expiration = clamp(deadline-now-60, 300, 7200)`，无 deadline 时 600；
  - 无 `dash` / 空候选 / 87007/87008 → `UnplayableMediaError`；
  - 日志不输出完整 URL（只打 host / endpoint）。
- 收藏夹：`get_library_playlists` 匿名直接空；登录后委托
  `catalog.iter_favorite_playlists`。`get_playlist` / `get_playlist_tracks` 委托
  `catalog.get_favorite_playlist` / `get_favorite_tracks`。
- 推荐/浏览：委托 `catalog.recommendation_rows` / `recommendation_items` /
  `browse_items`。
- 错误翻译（`BiliApiError` → MA 异常）：
  - `-352` → `ResourceTemporarilyUnavailable(backoff_time=30)`
  - `-412` → `ResourceTemporarilyUnavailable(backoff_time=60)`
  - `-404` / `62002` / `62004` → `MediaNotFoundError`
  - `87007` / `87008` → `UnplayableMediaError`
  - 网络 / 5xx 已由 client 抛 `ResourceTemporarilyUnavailable`，provider 原样放行。
- `unload()`：释放 client 引用并清空 view/artist/CDN 缓存。

### `tests/unit/test_provider.py`

44 个用例，全部 fake client / fake `mass` / fake aiohttp session，不联网，无新 fixture。
覆盖：

- config entries 的 key/默认值/quality options/`SECURE_STRING`/unofficial note；
- `setup()` 返回类型与 feature 集合；
- `handle_async_init()`：匿名补 buvid；有效凭据记录 user/mid；无效 nav 降级匿名；
  nav API 错误降级；网络错误保留凭据；手填 SESSDATA 用 nav 补 mid；
- search：媒体类型过滤、空 query、时长过滤、去重保留高播放、按 play 降序、limit 与
  `search_limit` 截断；
- get_track：`bvid#cid` 取对应 page 的 part、原始 id 保留、view 缓存命中、空 id；
- get_artist：内存名字 + `Bilibili UP 主 <mid>` 兜底；
- stream details：auto 选 FLAC、quality 档位上限（dolby/aac192/aac132/aac64）、
  无 cid 时 view 解析、`av<aid>#cid`、dict 形 flac、探测失败换 backup、全失败回首选、
  探测头 `Range`、expiration 上下限、关闭探测不发请求、空 dash/空候选、
  非 TRACK 媒体类型；
- 匿名收藏夹为空且不发请求；unload 清理；
- 错误翻译：-352/-412/-404/62002/62004/87007/87008、未知 code、网络错误透传；
- 推荐行 id。

## 测试与 lint 结果

```text
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python -m pytest tests/unit/test_provider.py -q
# 44 passed in 2.25s

docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python -m pytest tests -q
# 146 passed in 4.92s

docker exec -w /workspace bilibili-ma-dev /app/venv/bin/ruff check provider/bilibili/__init__.py tests/unit/test_provider.py
# All checks passed!
```

另外对这两个文件执行了 `ruff format`（仅格式化，无逻辑变化），格式检查通过。

## 与契约的偏差 / 实现选择

以下均为契约未写死、实现时做的选择，已尽量贴近 `docs/implementation-plan.md`：

1. **nav 网络错误不降级**：只有 nav 明确返回 `isLogin=false` 或抛 `BiliApiError`
   才降级匿名；`ResourceTemporarilyUnavailable`/aiohttp/Timeout 只 warning 并保留
   已保存凭据。理由：临时网络问题不等于凭据失效，直接丢弃会让收藏夹功能无故消失。
2. **未知时长保留**：`duration <= 0` 的搜索结果不做 min/max 过滤。契约只说时长过滤，
   计划写 `min_duration ≤ d ≤ max_duration`；这里选择不丢弃「API 没给时长」的条目。
3. **quality 上限兜底**：若配置上限把全部候选过滤掉（例如只给 FLAC 的视频 + `aac64`），
   会 debug 后回退到最高可用流，而不是让播放失败。正常存在低档流时严格按上限选。
4. **未知 BiliApiError code**：契约表未覆盖的 code 映射为
   `ResourceTemporarilyUnavailable(backoff_time=5)`（可重试），避免误报
   `MediaNotFoundError` 把条目从库中删掉。
5. **额外 CDN host 健康缓存**：`host -> (healthy, monotonic_expiry)`，TTL 用
   `constants.CDN_HEALTH_TTL`（10 分钟），来自 implementation-plan §5.4；契约只要求
   探测与 backup 兜底，未要求缓存。
6. **搜索 page_size**：请求 `clamp(max(wanted, 20), 20, 50)`，比最终返回多取一些，
   以防过滤/去重后不够数；最终仍严格截断到 `min(limit, search_limit)`。契约未规定
   page_size。
7. **AudioFormat 细节**：FLAC 96kHz/24bit、Dolby EAC3 48kHz/6ch、AAC 用
   `ContentType.M4A` 44.1kHz，`bit_rate` 取 bandwidth（kbps）。契约只要求选流，
   这些是给 MA 展示/质量评分的补充。Dolby 的 `ec-3` 映射到 `ContentType.EAC3`。
8. **`get_stream_details` 额外设置 `duration`**：取 `dash.duration`（秒），方便 MA
   排队与 seek；契约未禁止。
9. **`get_track` 同步 page duration**：带 cid 时除 name 外也把对应 P 的 duration 覆盖到
   Track（`parse_view` 多 P 默认用第 1 P 时长，不改会让第 2 P 显示错误）。
10. **测试不用 fixtures 文件**：全部用内存 fake payload；题目允许
    「tests/fixtures 或 fake 对象」。

## 注意事项（给 lead）

- `provider/bilibili/__init__.py` 现在会导入 MA 运行时（`music_assistant.constants`、
  `MusicProvider`），所以 `tests/unit` 里任何 `import bilibili` 都要求容器内环境
  （本仓库测试命令即如此）。
- 未执行、也不需要 git add/commit。
- `docs/contracts.md`、`docs/implementation-plan.md`、`docs/devlog.md` 均未修改。
