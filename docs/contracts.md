# 模块契约 v1（开发团队共用）

目标：MA **2.10.8**（兼容 2.10.7），`music-assistant-models==1.1.205`，Python 3.14。
所有实现必须能在 MA 2.10.x 的 `MusicProvider` 单类 API 下工作；**不要 import `music_assistant.models.media_capabilities`**。

## 文件所有权（避免并行冲突）

| 文件 | 负责人 | 说明 |
|---|---|---|
| `provider/bilibili/constants.py` | lead（已完成） | 常量、配置 key、ffmpeg headers、噪声词 |
| `provider/bilibili/auth.py` | lead（已完成） | 纯 Python 凭据模型 |
| `provider/bilibili/compat.py` | lead（已完成） | 运行时能力探测 |
| `provider/bilibili/wbi.py` | agent-client | WBI 签名纯函数 |
| `provider/bilibili/client.py` | agent-client | HTTP 客户端、错误、缓存 keys |
| `provider/bilibili/parsers.py` | agent-parsers | dict → MA 模型 |
| `provider/bilibili/setup_flow.py` | agent-auth | 二维码/手填/匿名流程 |
| `provider/bilibili/catalog.py` | agent-catalog | 收藏夹、推荐、browse 辅助函数 |
| `provider/bilibili/__init__.py` | agent-provider | provider 类与 feature 方法 |
| `tests/unit/*` | 各自模块作者 + agent-tests | 单元测试 |
| `docker/*`、`scripts/*`、`README.md` | lead | 环境与分发 |

> 每个 agent 只改自己名下的文件；需要改别人的文件时，在 `docs/devlog.md` 里记一条 TODO，交给 lead。

## 导入方向

```
auth.py        （无 MA import）
compat.py      （只 import music_assistant_models）
wbi.py         （无 MA import）
client.py      → auth, constants
parsers.py     → constants, music_assistant_models
catalog.py     → client, parsers, constants, music_assistant_models
setup_flow.py  → auth, client, constants, music_assistant.models.setup_flow
__init__.py    → 以上全部 + music_assistant.models.music_provider
```

## `wbi.py`

```python
MIXIN_KEY_ENC_TAB: list[int]  # 64 项，照抄 B 站

def mixin_key(img_key: str, sub_key: str) -> str: ...

def sign_params(
    params: Mapping[str, str | int],
    img_key: str,
    sub_key: str,
    *,
    wts: int | None = None,
) -> str:
    """返回完整的 query（含 wts 与 w_rid），调用方必须原样发送。"""
```

规则：补 `wts` → 按 key 排序 → 值去掉 `!'()*` → `quote(..., safe="")` → `md5(query + mixin_key)`。

## `client.py`

```python
class BiliApiError(Exception):
    code: int
    message: str
    endpoint: str

@dataclass(slots=True)
class AudioStream:
    stream_id: int
    bandwidth: int
    mime_type: str       # audio/mp4 / audio/flac ...
    codec: str           # aac / flac / eac3 / unknown
    url: str
    backups: list[str]

class BilibiliClient:
    def __init__(self, mass, credential: BilibiliCredential, logger: Logger | None = None): ...

    credential: BilibiliCredential

    async def close(self) -> None: ...                      # mass.http_session 是共享的，通常 no-op
    async def nav(self) -> dict: ...                         # 允许 code=-101，返回 data
    async def spi(self) -> dict: ...                         # {"b_3": ..., "b_4": ...}
    async def search_video(self, keyword: str, page: int = 1, page_size: int = 20) -> list[dict]: ...
    async def view(self, bvid: str) -> dict: ...             # data
    async def playurl(self, *, bvid: str | None = None, avid: int | None = None, cid: int) -> dict: ...
    async def ranking_region(self, rid: int = 3, day: int = 3) -> list[dict]: ...
    async def music_rank(self, ps: int = 30) -> list[dict]: ...
    async def new_music(self) -> list[dict]: ...
    async def rcmd(self, ps: int = 10) -> list[dict]: ...
    async def fav_folders(self, mid: int) -> list[dict]: ...
    async def fav_collected(self, mid: int, page: int = 1, page_size: int = 20) -> dict: ...
    async def fav_items(self, media_id: int, page: int = 1, page_size: int = 20) -> dict: ...
    async def qrcode_generate(self) -> dict: ...             # {"url": ..., "qrcode_key": ...}
    async def qrcode_poll(self, qrcode_key: str) -> tuple[int, dict[str, str]]: ...
        # 返回 (data.code, cookies)；cookies 至少含 SESSDATA/bili_jct/DedeUserID（成功时）
```

要求：

- 用 `mass.http_session`，不要自己建 aiohttp session。
- 每个请求带 `Referer: https://www.bilibili.com`、`Origin: https://www.bilibili.com`、`User-Agent: constants.USER_AGENT`；有凭据时加 `Cookie`。
- 搜索用 WBI：`/x/web-interface/wbi/search/type?search_type=video&keyword=...&page=...&page_size=...`。
- `playurl` 固定 `qn=0&fnver=0&fnval=16&fourk=1`，参数二选一（`bvid` 或 `avid`）加 `cid`。
- `nav` 的 `code=-101` 不是错误，必须返回 `data`（含 `wbi_img`）。
- `qrcode_poll` 的 HTTP 可能 200 但 `data.code` 是 `86101/86090/86038/0`；不要按 HTTP 错误处理。
- 失败统一抛 `BiliApiError`；provider 层负责翻译成 MA 异常。
- 限流：全局 `Throttler(3, 1.0)`；搜索额外 `Throttler(1, 1.0)`。
- WBI keys 内存缓存 6h + `asyncio.Lock`；不要用 `@use_cache`（那是 provider 层的东西）。
- 日志不允许输出完整带签名 URL；只输出 host/path。

## `parsers.py`

```python
def clean_title(raw: str) -> str: ...
def parse_duration(value: str | int) -> int: ...
def to_https(url: str) -> str: ...
def extract_em_tags(raw: str) -> str: ...                    # 去 <em> 等 HTML

def parse_search_item(item: dict, provider_instance: str) -> Track | None: ...
def parse_view(data: dict, provider_instance: str) -> Track: ...
def parse_fav_item(item: dict, provider_instance: str) -> Track | None: ...
def parse_fav_folder(item: dict, provider_instance: str, owner_name: str) -> Playlist: ...
def parse_music_center_item(item: dict, provider_instance: str) -> Track | None: ...
def parse_ranking_item(item: dict, provider_instance: str) -> Track | None: ...
```

映射要求：

- `Track.item_id = bvid`（多 P 用 `bvid#cid`）；`Track.provider = provider_instance`。
- `Track.name = clean_title(title)`；保留原始标题到 `metadata.description`。
- `Track.artists` 用 `ItemMapping(item_id=str(mid), provider=provider_instance, name=author)`。
- 封面：`//` → `https://`；`MediaItemImage(type=ImageType.THUMB, path=..., provider=..., remotely_accessible=True)`。
- `metadata.links` 放 `LinkType.WEBSITE` 的 B 站视频 URL。
- `metadata.popularity` 用 play 数（缺失为 None）。
- `Track.duration` 必须是 int 秒；解析 `"4:30"` / `"1:02:03"` / int。
- `provider_mappings={ProviderMapping(item_id=..., provider_domain="bilibili", provider_instance=..., available=True, url=...)}`。
- `parse_fav_folder` 返回 `Playlist(item_id=f"fav:{id}", name=title, owner=owner_name, is_editable=False, provider=..., provider_mappings={...})`。
- 跳过无效项返回 `None`，不要抛异常。

## `catalog.py`

```python
async def iter_favorite_playlists(client: BilibiliClient, provider_instance: str) -> AsyncIterator[Playlist]: ...
async def get_favorite_playlist(client: BilibiliClient, provider_instance: str, playlist_id: str) -> Playlist: ...
async def get_favorite_tracks(
    client: BilibiliClient, provider_instance: str, playlist_id: str, page: int = 0
) -> list[Track]: ...
def recommendation_rows(provider_instance: str) -> list[RecommendationFolder]: ...
async def recommendation_items(
    client: BilibiliClient, provider_instance: str, row_id: str
) -> UniqueList[MediaItemType | ItemMapping | BrowseFolder]: ...
async def browse_items(
    client: BilibiliClient, provider_instance: str, path: str
) -> Sequence[MediaItemType | ItemMapping | BrowseFolder]: ...
```

约定：

- `playlist_id` 形如 `fav:12345`；解析失败抛 `MediaNotFoundError`。
- 收藏夹分页：`pn` 从 1，`has_more` 继续；`position` 跨页从 1 连续递增。
- 失效稿件 `attr != 0` 跳过。
- 匿名（`client.credential.has_login == False`）：`iter_favorite_playlists` 直接 `return`，不报错。
- 推荐行 id：`rank_music` / `new_music` / `region_music`。
- browse 根路径为空或 `bilibili://`；子路径 `rank`、`new`、`favorites`。

## `setup_flow.py`

```python
async def run_setup(session: SetupSession) -> None: ...
```

流程：

1. FORM `choose`：`method` = `qr` / `manual` / `anonymous`（默认 `qr`）。
2. `anonymous` → `session.finish({})`。
3. `manual` → FORM `manual`：`sessdata`（必填）、`dedeuserid`（可选）→ 用 `BilibiliCredential` 组 JSON → `finish({"credential_json": ...})`。
4. `qr`：`client.qrcode_generate()` → segno 渲染 PNG data URI → `session.progress_until(_poll(...), step_id="scan_qr", image=..., expires_in=180)`；成功后把 cookies 合并 `spi()` 的 buvid → `finish({"credential_json": ...})`。
5. `segno` 缺失时回退到 `manual` 表单，并在 `description` 里给出安装提示。

## `__init__.py`

```python
SUPPORTED_FEATURES = {
    ProviderFeature.SEARCH,
    ProviderFeature.LIBRARY_PLAYLISTS,
    ProviderFeature.RECOMMENDATIONS,
    ProviderFeature.BROWSE,
}

class BilibiliProvider(MusicProvider):
    async def handle_async_init(self) -> None: ...
    async def get_config_entries(self) -> tuple[ConfigEntry, ...]: ...
    async def search(self, search_query, media_types, limit=5) -> SearchResults: ...
    async def get_track(self, prov_track_id: str) -> Track: ...
    async def get_artist(self, prov_artist_id: str) -> Artist: ...
    async def get_stream_details(self, item_id, media_type) -> StreamDetails: ...
    async def get_library_playlists(self) -> AsyncGenerator[Playlist]: ...
    async def get_playlist(self, prov_playlist_id: str) -> Playlist: ...
    async def get_playlist_tracks(self, prov_playlist_id, page=0) -> Sequence[PlaylistPlayableItem]: ...
    async def get_recommendations(self) -> list[RecommendationFolder]: ...
    async def get_recommendation_items(self, item_id) -> UniqueList: ...
    async def browse(self, path) -> Sequence: ...
    async def unload(self, is_removed: bool = False) -> None: ...
```

`get_stream_details` 规则：

- 解析 `bvid` / `cid`；无 `cid` 时 `view()` 取 `pages[0].cid`（也可支持 `bvid#cid`）。
- `playurl` → `dash`；候选来自 `audio`（list）、`dolby.audio`（list）、`flac.audio`（dict/list）。
- 按 `quality` 配置上限与 `AUDIO_ID_RANK` 排序；FLAC > Dolby > 30280 > 30232 > 30216。
- `verify_stream_url` 为真时，对首选 URL 做 `Range: bytes=0-1023` 探测（1.5s），失败换 `backupUrl`；全失败只 warning 并返回首选。
- `StreamDetails`：`HTTP`、`can_seek=True`、`allow_seek=True`、`expiration=clamp(deadline-now-60, 300, 7200)`、`extra_input_args=constants.FFMPEG_HEADERS`。
- 无 `dash` 或空 candidate → `UnplayableMediaError`。
- 日志不得出现完整 URL。

错误翻译：

| 条件 | 异常 |
|---|---|
| `-352` | `ResourceTemporarilyUnavailable(backoff_time=30)` |
| `-412` | `ResourceTemporarilyUnavailable(backoff_time=60)` |
| `-404/62002/62004` | `MediaNotFoundError` |
| `87007/87008` | `UnplayableMediaError` |
| 网络/5xx | `ResourceTemporarilyUnavailable` |

## 测试要求

- 纯函数（wbi/parsers/auth）必须有单元测试，不联网。
- 测试用 `pytest`，asyncio 用 `pytest-asyncio`（`asyncio_mode=auto`）。
- fixture 放 `tests/fixtures/*.json`；不要提交真实 Cookie、SESSDATA、带签名 URL。
- 运行：容器内 `python -m pytest -q`；lint：`ruff check provider tests`。

## 提交纪律

- 每完成一个模块，在自己的分支/工作区跑测试，并在 `docs/devlog.md` 追加一行记录（时间、模块、结果）。
- 不要 `git commit`；lead 统一提交。
- 不要修改 `docs/implementation-plan.md` / `docs/contracts.md`。
