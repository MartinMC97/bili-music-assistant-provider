# notes-client — WBI 签名 + Bilibili HTTP 客户端

负责人：client
完成文件（仅这些）：

- `provider/bilibili/wbi.py`
- `provider/bilibili/client.py`
- `tests/unit/test_wbi.py`
- `tests/unit/test_client.py`

## 摘要

### wbi.py

- `MIXIN_KEY_ENC_TAB`：64 项，与 B 站 web player 的 `mixinKeyEncTab` 一致；单测校验了长度、0..63 全排列和首尾片段。
- `mixin_key(img_key, sub_key)`：`img_key + sub_key` 按表置换后取前 32 字符。
- `sign_params(params, img_key, sub_key, *, wts=None)`：
  - 未传 `wts` 时补当前 Unix 秒；params 里已有的 `wts` 会被覆盖；
  - 按 key 排序；
  - 值先去 `!'()*`，再 `quote(..., safe="")`；
  - `md5(query + mixin_key)` 生成 `w_rid`，返回完整 query；
  - key 也做了 `quote(safe="")`（B 站 key 都是 ASCII，不影响黄金向量）。
- 黄金向量已写进测试并通过：
  - `img_key=7cd084941338484aae1ad9425b84077c`
  - `sub_key=4932caff0ff746eab6f01bf08b70ac45`
  - `wts=1700000000`
  - `mixin_key == ea1db124af3c7062474693fa704f4ff8`
  - `w_rid == afa4ee1349451cece35c24b7f0e6be04`

### client.py

- `BiliApiError`：保存 `code` / `message` / `endpoint`（只存 path，不存完整 URL）。
- `AudioStream`：`@dataclass(slots=True)`，字段 `stream_id/bandwidth/mime_type/codec/url/backups`。
- `BilibiliClient`：
  - 只用 `mass.http_session`，`close()` 是 no-op；
  - 所有请求带 `Referer`、`Origin`、`User-Agent`，有凭据时加 `Cookie`；
  - 全局 `Throttler(3, 1.0)`，搜索额外 `Throttler(1, 1.0)`；
  - WBI keys：`nav()` 取 `data.wbi_img`，允许 `code=-101`；内存缓存 6h + `asyncio.Lock`，双重检查避免并发重复取；
  - 搜索 query 由 `sign_params` 生成，并用 `yarl.URL(..., encoded=True)` 原样发送，避免二次编码；
  - 已实现契约中的全部方法：`nav/spi/search_video/view/playurl/ranking_region/music_rank/new_music/rcmd/fav_folders/fav_collected/fav_items/qrcode_generate/qrcode_poll`；
  - `qrcode_poll` 返回内层 `data.code`，并从 `resp.cookies`（以及 `data.cookie_info.cookies` 兜底）收集 Cookie；
  - 日志只输出 endpoint path，不输出完整 URL/Cookie。
- 端点与参数：
  - search：`/x/web-interface/wbi/search/type`
  - view：`/x/web-interface/view?bvid=`
  - playurl：`/x/player/playurl?bvid|avid&cid&qn=0&fnver=0&fnval=16&fourk=1`
  - ranking：`/x/web-interface/ranking/region?rid=3&day=3`
  - music rank：`/x/centralization/interface/music/comprehensive/web/rank?pn=1&ps=`
  - new music：`/x/centralization/interface/new/music`
  - rcmd：`/x/web-interface/index/top/rcmd?ps=`
  - fav folders：`/x/v3/fav/folder/created/list-all?up_mid=`
  - fav collected：`/x/v3/fav/folder/collected/list?up_mid=&pn=&ps=`
  - fav items：`/x/v3/fav/resource/list?media_id=&pn=&ps=&platform=web`
  - spi：`/x/frontend/finger/spi`
  - qrcode：passport `/x/passport-login/web/qrcode/generate` 与 `/poll?qrcode_key=`

## 测试结果

- `docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python -m pytest tests/unit/test_wbi.py tests/unit/test_client.py -q`
  - **26 passed**
- 全量测试（含并行 agent 的 62 个用例）：
  - `docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python -m pytest -q`
  - **62 passed**
- lint：
  - `ruff check provider/bilibili/wbi.py provider/bilibili/client.py tests/unit/test_wbi.py tests/unit/test_client.py`
  - **All checks passed**

测试全部使用 fake `mass.http_session` 或 `tests/fixtures`，不联网。

## 与契约的偏差 / 说明

1. 网络错误、超时、HTTP 5xx/429 在 client 内直接翻译为 `ResourceTemporarilyUnavailable`（backoff 5/30s）；API `code != 0` 仍抛 `BiliApiError`。任务说明允许由 client 或调用方处理网络异常。
2. `AudioStream.backups` 使用 `field(default_factory=list)`，比契约多一个空列表默认值，方便构造；字段顺序与契约一致。
3. `mixin_key` 对不足 64 字符的 key 做了补零防御，避免损坏的 nav 响应触发 `IndexError`；合法 64 字符输入行为与 B 站一致。
4. `qrcode_poll` 外层 `code != 0` 会先抛 `BiliApiError`；正常扫码状态（内层 86101/86090/86038/0）不当作错误。
5. 未在 client 层做 `view`/搜索等结果缓存（按契约由 provider 层 `@use_cache` 负责）；仅 WBI keys 在 client 内缓存 6h。
6. 未修改任何他人文件、docs 或其它模块；未执行 git add/commit。
