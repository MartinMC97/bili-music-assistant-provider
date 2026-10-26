# Bilibili × Music Assistant Provider 实现计划

> 版本：v3 · 2026-10-25
> 目标环境：**Music Assistant 2.10.8**（当前最新稳定版；兼容 2.10.7；`music-assistant-models==1.1.205`），Python 3.14
> 2.11 目前只有 **beta/dev 预发布**（`2.11.0b6/b7`、`2.11.0.dev*`），不是当前目标，只做前瞻兼容。
> 技术依据：[可行性与方案](./bilibili-provider-plan.md)（含完整实测记录）
> 本文自包含：调研结论、已锁定决策、兼容性设计、工作分解、验收标准。
> **状态：仅计划，不含实现代码。**

> **实现进度（2026-10-25 23:58）**：M0/M1 完成并在 MA 2.10.8 容器内跑通搜索 + 播放 + seek；
> M2 的匿名/手填/扫码 setup flow 已接入，匿名路径 E2E 通过；M4 收藏夹代码与单测完成，待真实账号验收；
> M6 的本地 Docker / Dockerfile 完成；M8 未开始。详见 `docs/devlog.md` 与 `artifacts/`。

---

## 0. 决策记录

| ID | 决策 | 对实现的影响 |
|---|---|---|
| **D1** | 当前 MA 跑在 Docker，但 provider 必须与**安装方式解耦** | 代码零绝对路径、零原生依赖、零运行时写盘；交付提供多通道；HAOS add-on 只能靠上游合并覆盖 |
| **D2** | **允许登录** B 站账号 | 二维码登录进入 v0.2；但登录是**增强层**，匿名路径必须始终可用 |
| **D3** | **歌词不是硬需求** | 不声明 `LYRICS`、不写 `lyrics.py`、不接第三方歌词源；接受「无歌词」 |
| **D4** | 不下载、不落盘、不转存 | 只做流式播放，符合 MA 的 streaming provider 政策 |
| **D5** | 先独立仓库，按上游标准写 | 目录结构、测试、lint 与上游一致，后续可平滑提 PR |
| **D6** | **先本地开发，暂不发布 PyPI** | v1.0 前只做 Docker/bind mount/venv；PyPI + plugin-manager 放 v1.1 |
| **D7** | **收藏夹必需** | 收藏夹从「可选」提升为 v0.3 必做里程碑，排在推荐/浏览之前 |
| **D8** | 目标版本 **MA 2.10.8**（最新稳定；兼容 2.10.7，同 API 线） | 按 `MusicProvider` 单类 API 写（2.10.x 无 base mixin）；顺带兼容 2.11 beta/dev，但不作为目标；CI 主跑 2.10.7 + 2.10.8 |

---

## 1. 调研结论（实现前提）

以下结论均来自对 **Music Assistant 2.10.8 源码**（`server` tag `2.10.8`，provider API 与 2.10.7 一致 + `models==1.1.205`）、`HanversionOvO/BiliMusic` 的逐文件核对与真实请求验证（2026-10，非国内 IP）。2.10.7 / 2.10.8 均为 128 个 provider，没有 bilibili。

> 版本口径：截至 2026-10-25，**最新稳定版是 2.10.8**；2.11 只有 beta/dev 预发布（`2.11.0b6/b7`、`2.11.0.dev*`）。已核对 2.10.7 与 2.10.8 的 `music_provider.py`、`setup_flow.py`、`ffmpeg.py` **完全一致**，`models` 都锁 `1.1.205`；两版仅 `audio.py` 有补丁差异。

### 1.1 已验证可用

| # | 结论 | 证据 | 实现含义 |
|---|---|---|---|
| E1 | 匿名可拿到 192k AAC | `playurl?fnval=16` 返回 `30216/30232/30280` 三档，`audio/mp4`，AAC 48kHz 立体声 | 登录不是前提，可先做匿名 |
| E2 | 音频 CDN 备份链强制校验 Referer | 主链 `*.mcdn.bilivideo.cn` → 206；备份链 `*.bilivideo.com` → **403** | 必须注入请求头 |
| E3 | ffmpeg 可用 `-headers` 打通 | 不带头 → `403 Forbidden`；带头 → 正常解出 AAC；`-ss 60` seek 正常 | `StreamDetails.extra_input_args` 方案成立 |
| E4 | MA 会把 `extra_input_args` 放到 `-i` 之前 | 2.10.7 `helpers/ffmpeg.py:545` | 无需自定义流 |
| E5 | 2.10.x 没有 MP3 seek 探测 helper | `helpers/mp3.py` 在 2.10.7/2.10.8 不存在（目前只有 2.11 beta/dev 才有） | 不依赖它；m4s/AAC 走 ffmpeg 原生探测 |
| E6 | 流地址过期会重新取 | 2.10.7 `controllers/streams/audio.py:3340`（`created_at + expiration`） | 用 `expiration` 控制签名 URL 生命周期 |
| E7 | WBI 签名可在服务端复现 | Python 实现后 `wbi/search/type` 返回 `code 0`、1000 条结果 | 搜索可服务端化 |
| E8 | 榜单/推荐接口匿名可用 | 综合榜、新歌、分区榜、首页推荐全部 `code 0` | 推荐行低风险，放 v0.3 |
| E9 | 封面不防盗链 | 直接 GET 返回 `200 image/jpeg` | 封面可远程引用，无需代理 |
| E10 | MA 播放路径无额外预检 | 播放直接交给 ffmpeg，不自己 HEAD/GET | 不用为「MA 先探测」做适配 |

### 1.2 已验证受限 / 失败

| # | 结论 | 证据 | 应对 |
|---|---|---|---|
| L1 | UP 主空间接口被风控 | `/x/space/wbi/arc/search` 始终 `-352`，补 `buvid3/4`、`Origin`、`Sec-Fetch-*` 也无效 | **不声明 `ARTIST_TRACKS`**；Artist 只做展示；v0.x 不做 UP 主页 |
| L2 | 匿名无高音质扩展字段 | `dash.dolby = {"type": 0, "audio": null}`、`dash.flac = null` | 登录 + 会员权益才读；不绕过 |
| L3 | 高音质字段结构与主音频不同 | 匿名时 `dolby.audio=null`、`flac=null`；按 B 站返回结构，`dolby.audio` 为数组、`flac.audio` 为对象 | 选择器同时兼容 `list` / `dict`，不假设只有 `dash.audio` |
| L4 | MA 自带歌词兜底对 B 站曲目无效 | LRCLIB `/get` 要求歌名+歌手+时长精确匹配；B 站 artist 是 UP 主 | D3：不做歌词，接受无歌词 |
| L5 | 搜索结果含 HTML 与脏数据 | 标题带 `<em class="keyword">`；时长 `"4:30"`；封面 `//i0.hdslb.com/...` | 解析层统一清洗 |
| L6 | B 站没有面向第三方的音乐 API | 只有视频 DASH 音轨；`auXXXX` 音频区已退出主流程 | 定位就是「视频音轨」 |

### 1.3 由此推导出的六条硬约束

1. **必须带 header**：`Referer` + `Origin` + 桌面 UA，否则备份 CDN 直接 403。
2. **必须做 WBI + 节流**：搜索/空间接口要签名；高频会 `-412`。
3. **不能缓存音频 URL**：签名与 IP/时间绑定，只在播放瞬间取。
4. **不能依赖 UP 主接口**：`-352` 无解，Artist 能力降级。
5. **必须兼容三种音频字段形态**：`audio` / `dolby.audio` / `flac.audio`。
6. **搜索结果是视频**：必须做标题清洗、时长过滤、去重、打分。

---

## 2. 兼容性设计（D1 的核心）

### 2.1 安装形态矩阵

| 分发通道 | Docker | HA Container | HAOS Add-on | 裸机 / venv | 升级后保留 | 维护成本 |
|---|---|---|---|---|---|---|
| **上游合并** | ✅ | ✅ | ✅ | ✅ | ✅ | 低（随版本） |
| Dockerfile 覆盖 | ✅ | ✅ | ❌ | ❌ | ✅（重建镜像） | 中 |
| bind mount | ✅ | ✅ | ❌ | ❌ | ✅（compose 保留） | 低 |
| venv 安装脚本 | ❌ | ❌ | ❌ | ✅ | ❌（MA 升级需重装） | 低 |
| pip + plugin-manager（v1.1） | ✅（自定义镜像） | ✅ | ❌ | ✅ | ⚠️ | 中 |
| 自定义 HA add-on | ❌ | ❌ | ⚠️ | ❌ | ⚠️ | 高 |

**结论**：真正「全安装方式」只有**上游合并**一条路。因此本计划把「可上游」作为一等目标，Docker/venv 作为过渡通道；HAOS add-on 在 v1 前**明确不支持**，不承诺。

### 2.2 代码级可移植约束（写进 Code Review checklist）

1. **零绝对路径**：不引用 `/app`、`/data`、`site-packages`；需要静态资源时用 `importlib.resources`。
2. **零运行时写盘**：不写缓存文件、不建目录；只读挂载（`:ro`）也能跑。
3. **零原生依赖**：只用 MA 自带的 `aiohttp` / stdlib；二维码用纯 Python 的 `segno`（可选依赖，缺失可降级）。
4. **零 shell 依赖**：不 `subprocess` 调 `uv`/`pip`/`ffmpeg`（ffmpeg 由 MA 调）。
5. **不假设 root / 可写 venv**：装不上依赖时要有降级路径。
6. **不依赖安装方式**：不读环境变量判断 Docker/HA；行为一致。
7. **只用公开契约**：`music_assistant_models.*` + 文档化 helper（`use_cache`、`Throttler`）；不 import 控制器私有模块。
8. **多实例安全**：`multi_instance: true`；client/throttler/缓存按实例隔离，不用模块级可变全局。
9. **Python 版本**：目标 **3.14**（MA 2.10.7 的 `requires-python` 就是 `>=3.14`）；不用 3.15 新特性。
10. **失败可解释**：任何 API/模型不兼容都要抛带修复建议的异常，不静默降级成「没结果」。

### 2.3 版本兼容与能力探测

**2.10.x 与 2.11 beta/dev 的架构差异**（已核对源码）：

| 项目 | MA 2.10.x（2.10.7 / 2.10.8，目标） | MA 2.11 beta/dev（预发布，非目标） |
|---|---|---|
| provider 基类 | `MusicProvider(Provider)` 单类 | `MusicProvider(MediaCatalogMixin, RecommendationsMixin, AudioStreamMixin, Provider)` |
| 能力 mixin | **不存在** `models/media_capabilities.py` | 有 |
| `StreamDetails.extra_input_args` | ✅（models 1.1.205） | ✅ |
| `helpers/mp3.py` / `ffmpeg_http_headers` | ❌ 不存在 | ✅ |
| `Throttler.acquire()` | 无参数 | 可选 `priority` |
| `ConfigEntryType.FOLDER` | ❌ 不存在 | ✅ |
| Python | `>=3.14` | `>=3.14` |

**兼容策略**：

1. **直接继承 `MusicProvider`，绝不 import `media_capabilities`**——它在 2.10.x 不存在；而 2.11 beta/dev 的 `MusicProvider` 仍保留全部方法，单类写法两边都成立。
2. **只用稳定线都有的 API**：`@use_cache(seconds)`、`Throttler(rate_limit, period)`、`self.mass.http_session`、`self.get_setup_value()`、`self.config.get_value()`。
3. **不依赖 `helpers/mp3.py`**（2.10.x 没有）；m4s/AAC 不需要它的 seek 探测。
4. **不使用 `ConfigEntryType.FOLDER`** 等 2.11 beta/dev 新增枚举。
5. CI 矩阵：**2.10.7 + 2.10.8**（都必须过）；2.11 dev 只做**非阻塞**前瞻检查。

```python
# compat.py（示意）
from dataclasses import fields

from music_assistant_models.streamdetails import StreamDetails


def check_compat() -> list[str]:
    issues: list[str] = []
    if "extra_input_args" not in {f.name for f in fields(StreamDetails)}:
        issues.append(
            "当前 music-assistant-models 缺少 StreamDetails.extra_input_args，"
            "无法向 ffmpeg 注入 Referer。需要 MA >= 2.10.7（models >= 1.1.205）。"
        )
    return issues
```

- 在 `handle_async_init()` 里调用；有 issue 就抛 `ProviderUnavailableError`，日志给出明确升级建议。
- **不做 CUSTOM 流回退**（会翻倍播放代码）；最低支持版本定死为 **MA 2.10.7**。

### 2.4 依赖策略

| 阶段 | `manifest.requirements` | 理由 |
|---|---|---|
| v0.1（匿名） | `[]` | 离线/受限环境也能装；搜索播放不需要额外包 |
| v0.2+（独立分发） | **仍为 `[]`**，`segno` 由 Dockerfile 预装或用户手动安装 | MA 在加载 provider 前会**强制安装** manifest 里的 requirements，安装失败会导致 provider 直接加载失败；可选依赖不能拖垮主功能 |
| 上游 PR | `["segno==1.6.6"]` | 上游能控制镜像与 CI；`segno` 纯 Python、无原生依赖 |

实现方式：

- `setup_flow.py` 里**延迟 import** `segno`；缺失时把流程降级为「表单 + 手动填 SESSDATA」，并提示安装命令。
- Dockerfile 预装 `segno==1.6.6`，让扫码开箱即用。
- 裸机用户：`<MA venv>/bin/pip install segno==1.6.6`（写进文档）。
- 任何阶段都不引入 `bilibili-api-python` 等第三方 SDK：API 只有几个端点，自己封装更可控、更可移植。

### 2.5 分发通道与仓库结构

```text
bilibili-ma-provider/
├── provider/
│   └── bilibili/              # 唯一源码树，目录名 = provider domain
│       ├── __init__.py
│       ├── manifest.json
│       ├── compat.py
│       ├── constants.py
│       ├── client.py
│       ├── auth.py
│       ├── parsers.py
│       ├── setup_flow.py      # v0.2
│       ├── strings.json
│       └── icon.svg
├── docker/
│   ├── Dockerfile
│   └── compose.override.yml
├── scripts/
│   ├── dev.sh                 # 本地开发：mount + 重启 + 日志
│   ├── install_venv.py
│   └── uninstall_venv.py
├── tests/
│   ├── unit/
│   └── fixtures/              # 录制的真实响应，测试不联网
├── pyproject.toml             # v1.1：pip + plugin-manager 通道（先留空壳）
└── docs/
    ├── bilibili-provider-plan.md
    └── implementation-plan.md
```

- **唯一源码树**，不复制代码；Docker/venv/上游都用 `provider/bilibili/`。
- 所有内部 import 用相对路径（`from .client import ...`），这样既能进树，也能被 v1.1 的 plugin-manager 当独立包装。
- **v1.0 不做 pip 包**（D6）。`pyproject.toml` 先留空壳，M8 再补：安装为顶层包 `bilibili`，发行名 `bilibili-ma-provider`；文档需提示同名包冲突。

---

## 3. 目标架构

### 3.1 模块图

```text
MA Core
  ├─ search() ──────────────► BilibiliProvider.search
  ├─ get_track() ───────────► BilibiliProvider.get_track
  └─ get_stream_details() ──► BilibiliProvider.get_stream_details
                                   │
                                   ├─ BilibiliClient ──► api.bilibili.com（WBI/签名/节流/缓存）
                                   ├─ parsers        ──► Track / Artist / Playlist
                                   └─ auth           ──► Cookie / 二维码登录
                                   │
                                   ▼
                        StreamDetails(path=CDN m4s,
                          extra_input_args=["-headers", "Referer: ...\r\n..."])
                                   │
                                   ▼
                        MA ffmpeg ──► CDN（AAC / FLAC / Dolby）
```

### 3.2 模块职责

| 模块 | 职责 | 不做什么 |
|---|---|---|
| `__init__.py` | `setup()`、`SUPPORTED_FEATURES`、provider 类、feature 方法 | 不写业务解析 |
| `client.py` | HTTP、WBI、节流、缓存、错误翻译、重试 | 不构造 MA 模型 |
| `parsers.py` | dict → `Track`/`Artist`/`Playlist`；标题清洗、时长解析 | 不发请求 |
| `auth.py` | 凭据模型、Cookie 组装、有效性校验、过期降级 | 不弹 UI |
| `setup_flow.py` | 二维码生成、轮询、错误码、凭据落库 | 不参与播放 |
| `compat.py` | 模型/版本能力探测 | 不处理业务 |
| `constants.py` | 域名、档位、噪声词、限流参数 | 不放逻辑 |

> 2.10.x 的 `MusicProvider` 是单类（`MusicProvider(Provider)`）；2.11 beta/dev 才拆成 mixin。provider 类**只继承 `MusicProvider`**，不直接 import `MediaCatalogMixin`/`AudioStreamMixin`，两条线都能加载。

### 3.3 生命周期

```text
setup()                → 构造 provider
handle_async_init()    → compat 检查 → 读取 setup_data/config → 建 client/throttler
                         → （可选）nav 校验登录 → 失败则降级匿名
get_stream_details()   → playurl → 选流 → 探测 → StreamDetails
unload()               → 关闭 client（若自建 session）、清缓存
```

原则：**初始化阶段不允许因为「登录失效」而让 provider 不可用**；只有「模型不兼容」才拒绝加载。

---

## 4. 契约

### 4.1 Feature 声明（按版本）

| Feature | v0.1 | v0.2 | v0.3 | v1.0 | 说明 |
|---|---|---|---|---|---|
| `SEARCH` | ✅ | ✅ | ✅ | ✅ | 核心 |
| `LIBRARY_PLAYLISTS` | ❌ | ❌ | ✅ | ✅ | **收藏夹（D7 必需）**，只读 |
| `RECOMMENDATIONS` | ❌ | ❌ | ❌ | ✅ | 榜单/新歌 |
| `BROWSE` | ❌ | ❌ | ❌ | ✅ | 榜单/新歌/收藏夹树 |
| `LYRICS` | ❌ | ❌ | ❌ | ❌ | D3：不做 |
| `ARTIST_TRACKS` | ❌ | ❌ | ❌ | ❌ | L1：接口被风控 |
| `PLAYLIST_CREATE/EDIT` | ❌ | ❌ | ❌ | ❌ | 不做写操作 |

> 收藏夹依赖登录态：匿名时 `get_library_playlists()` 返回空，不报错。

`manifest.json` 基线（独立分发阶段 `requirements` 留空，理由见 §2.4）：

```json
{
  "type": "music",
  "domain": "bilibili",
  "stage": "alpha",
  "name": "Bilibili",
  "description": "Bilibili 视频音轨作为音乐源（非官方）",
  "codeowners": ["@your-handle"],
  "requirements": [],
  "documentation": "https://github.com/MartinMC97/bili-music-assistant-provider",
  "multi_instance": true
}
```

### 4.2 数据映射

| Bilibili | MA | 规则 |
|---|---|---|
| `bvid` | `Track.item_id` | 主键；多 P 用 `bvid#cid` |
| `title` | `Track.name` | 去 `<em>`、去噪声、`html.unescape`、trim |
| `owner.mid` / `owner.name` | `Track.artists[0]` | 用 `ItemMapping`，语义是 UP 主 |
| `pic` | `metadata.images` | `//` → `https://` |
| `duration` | `Track.duration` | 解析 `mm:ss` / `hh:mm:ss` |
| `play` / `stat.view` | `metadata.popularity` | 排序辅助 |
| `pubdate` | `metadata.release_date` | `datetime.fromtimestamp` |
| `tag` | `metadata.genres` | 逗号拆分 |
| 视频 URL | `metadata.links` | `LinkType.WEBSITE` |
| `cid` | `ProviderMapping.details` | JSON 字符串，播放时解析 |

`Track` 必须带 `provider_mappings`（`MediaItem` 的必填字段），`provider_domain` 用 `self.domain`。

### 4.3 `item_id` 规则

| 场景 | 格式 | 解析 |
|---|---|---|
| 单 P 视频 | `BV1xxxxxxxxx` | `#` 不存在 → 调 `view` 取 `pages[0].cid` |
| 多 P / 指定分 P | `BV1xxxxxxxxx#123456` | `#` 后为 `cid` |
| 音乐中心（无 bvid） | `av123456#789012` | `av` + `aid` + `cid` |

- 搜索结果**不预取 cid**（否则一次搜索 20 次 `view`）。
- `view` 结果 `@use_cache(24h)`；`playurl` 不缓存。
- `get_track()` 对带 `cid` 的 id 从 `pages` 里找对应标题；找不到则回退主标题。
- **Artist 最小实现**：`get_artist(mid)` 返回基础 `Artist`（名字、头像、B 站链接），但**不声明 `ARTIST_TRACKS`**。否则用户在 MA 里点艺术家会撞上 `NotImplementedError`。

### 4.4 `StreamDetails` 规格

| 字段 | 值 | 说明 |
|---|---|---|
| `provider` | `self.instance_id` | |
| `item_id` | 原始 item_id | |
| `media_type` | `MediaType.TRACK` | |
| `stream_type` | `StreamType.HTTP` | 不用 CUSTOM |
| `path` | `baseUrl` | 服务端专用，序列化时被丢弃 |
| `audio_format.content_type` | `MP4` / `FLAC` | 按 `mimeType` |
| `audio_format.codec_type` | `AAC` / `FLAC` / `EAC3` | 按 `codecid`/`mimeType` |
| `audio_format.bit_rate` | `bandwidth // 1000` | kbps |
| `duration` | 曲目时长 | |
| `can_seek` / `allow_seek` | `True` / `True` | CDN 支持 Range（实测） |
| `expiration` | `clamp(deadline - now - 60, 300, 7200)` | URL 自带 `deadline` |
| `extra_input_args` | `["-headers", "Referer: ...\r\nOrigin: ...\r\nUser-Agent: ...\r\n"]` | 核心 |
| `data` | `{"host": ..., "backup": [...]}` | 不序列化，便于调试 |

播放并发：覆盖 `max_concurrent_streams` property，建议返回 `3`（B 站并发流限制不明确，保守取值；MA 会用它做信号量）。

### 4.5 错误映射

| B 站返回 | MA 异常 | 备注 |
|---|---|---|
| `-101` | `LoginFailed`（仅登录态操作）/ 匿名忽略 | 启动时降级，不抛 |
| `-352` | `ResourceTemporarilyUnavailable(backoff_time=30)` | 风控，退避 |
| `-412` | `ResourceTemporarilyUnavailable(backoff_time=60)` | 请求被拒 |
| `-404` / `62002` / `62004` | `MediaNotFoundError` | 稿件不存在/不可见 |
| `87007` / `87008` | `UnplayableMediaError` | 充电/大会员专属 |
| `dash` 为空 | `UnplayableMediaError` | 互动视频/预告 |
| CDN `403/404` | 先换 backup；仍失败 → `AudioError` | 带日志（不打 URL） |
| 超时 / 5xx | `ResourceTemporarilyUnavailable` | 由 MA 重试 |

### 4.6 配置项

| key | 类型 | 默认 | 阶段 | 说明 |
|---|---|---|---|---|
| `quality` | select | `auto` | v0.1 | `auto` / `flac` / `dolby` / `aac192` / `aac132` / `aac64` |
| `min_duration` | int | 60 | v0.1 | 过滤短视频 |
| `max_duration` | int | 900 | v0.1 | 过滤长循环/直播回放 |
| `search_limit` | int | 20 | v0.1 | 单次搜索条数 |
| `verify_stream_url` | bool | true | v0.1 | 播放前做 1KB Range 探测 |
| `sessdata_manual` | secure string | 空 | v0.2 | 手填兜底（setup_data 优先） |
| `credential_json` | setup_data | 空 | v0.2 | 扫码登录结果 |
| `unofficial_provider_note` | alert | — | v0.1 | MA 现成提示 |

凭据不写日志、不进异常信息、不出现在 `metadata`。

---

## 5. 关键实现要点

### 5.1 WBI 签名

- 从 `/x/web-interface/nav` 取 `wbi_img.img_url/sub_url`，截取文件名作为 key。
- **`nav` 未登录返回 `code=-101`，但 `data.wbi_img` 仍有效**：不能按通用错误处理。
- `img_key/sub_key` 每日轮换：`@use_cache(6h)` + `asyncio.Lock` 防并发重复取。
- 参数处理顺序：补 `wts` → 按 key 排序 → 去 `!'()*` → URL encode → 拼 `w_rid=md5(query + mixin_key)`。
- **签好名的 query 原样发出**，禁止二次编码。

### 5.2 登录（v0.2）

**流程**

1. `GET https://passport.bilibili.com/x/passport-login/web/qrcode/generate` → `{url, qrcode_key}`
2. `segno.make(url).save(buf, kind="png", scale=6, border=2)` → `data:image/png;base64,...`
   （`segno` 缺失时降级为表单：展示二维码内容/登录说明，让用户手填 SESSDATA）
3. `session.progress_until(_poll(qrcode_key), step_id="scan_qr", image=..., expires_in=180)`
4. 轮询：`86101` 未扫、`86090` 已扫待确认、`0` 成功、`86038` 过期
5. 成功：从响应 Cookie 取 `SESSDATA`、`bili_jct`、`DedeUserID`；缺 `buvid3/4` 时用 `/x/frontend/finger/spi` 补
6. `session.finish({"credential_json": json.dumps(...)})`
7. 流程里保留「以匿名方式继续」：`session.finish({})`，provider 无凭据加载，功能降级但可用。

**运行时**

- Cookie 头：`SESSDATA=...; bili_jct=...; DedeUserID=...; buvid3=...; buvid4=...`。
- 启动时 `nav` 校验一次；失效则**降级匿名**并 warning，不阻塞 provider。
- 过期不自动续期（B 站无公开 refresh 流程）；提示用户重新扫码。
- 手填 `sessdata_manual` 作为二维码不可用时的兜底。

**风险控制**

- 默认低频（3 req/s 上限），不做写操作，避免账号被限。
- 文档明确建议「专用小号」；登录失败不影响匿名使用。

### 5.3 音质选择

候选收集（必须兼容三种形态）：

```python
cands = []
cands += dash.get("audio") or []                 # list
cands += (dash.get("dolby") or {}).get("audio") or []   # list
flac_audio = (dash.get("flac") or {}).get("audio")      # dict 或 list
if isinstance(flac_audio, dict):
    cands.append(flac_audio)
elif isinstance(flac_audio, list):
    cands += flac_audio
```

- 归一化成统一结构：`{id, bandwidth, mime, codec, url, backups}`。
- 按配置上限过滤：`aac192` 只允许 ≤30280；`auto` 取最高可用。
- 排序：FLAC（无损）优先于 Dolby（多声道）优先于高码率 AAC；可用 `quality=auto` 时默认 FLAC > AAC。
- 会员字段为 `null` 时自然回退，不报错。
- `codec` 映射：`audio/mp4` → AAC/MP4；`audio/flac` → FLAC；Dolby 可能是 `ec-3`，映射到 `ContentType.EAC3`。

### 5.4 CDN 兜底

MA 在流错误时**复用同一份 `StreamDetails`**，不会重新调用 provider，所以必须自己兜：

1. **探测**（`verify_stream_url=true`）：对首选 URL 发 `Range: bytes=0-1023`，超时 1.5s；`200/206` 视为健康。
2. **候选顺序**：`baseUrl` → `backupUrl[0]` → `backupUrl[1]`，最多 3 个。
3. **健康缓存**：`host → (ok, ts)`，TTL 10 分钟；已知不健康的 host 直接跳过探测。
4. **全部失败**：记录 warning（只打 host，不打带签名的 URL），仍返回首选 URL，交给 ffmpeg 最后一搏。
5. **禁止**把探测失败当成 `UnplayableMediaError`（可能只是瞬时网络问题）。

### 5.5 节流与缓存

| 对象 | 策略 |
|---|---|
| API 全局 | `Throttler(rate_limit=3, period=1.0)` |
| 搜索 | 单独 `Throttler(rate_limit=1, period=1.0)` |
| `nav` / WBI keys | `@use_cache(6h)` |
| `view` | `@use_cache(24h)` |
| 搜索 | `@use_cache(600)` |
| 榜单/新歌 | `@use_cache(3600)` |
| 收藏夹 | `@use_cache(300)` |
| `playurl` | **不缓存** |

限流与缓存都要按 provider 实例隔离，避免多实例叠加。

实现注意：`@use_cache` 依赖 `self.mass.cache`，并会按返回类型注解反序列化。建议只把它用在 **provider 层返回 MA 模型** 的方法上；client 层返回的原始 `dict` 要么显式读写 `self.mass.cache`（自行 JSON 序列化），要么不加缓存，避免反序列化踩坑。

### 5.6 搜索清洗

1. `html.unescape` + 去 `<em ...>` / 其他标签；
2. 噪声词（移植 BiliMusic 的 `NOISE` / `CJK_NOISE`）：`官方MV`、`Hi-Res`、`4K修复`、`完整版`、`动态歌词`、`纯享`、`翻唱`（视配置）等；
3. 时长过滤：`min_duration ≤ d ≤ max_duration`；
4. 去重：`normalize(title) + author`，保留 `play` 最高；
5. 打分：关键词命中 > 标题干净度 > 播放量；
6. 截断 `search_limit`。

> 不做「歌手实体抽取」：误判率高于收益；Artist 保持 UP 主语义。

### 5.7 多 P / 合集

- `view.pages` 每 P 可成独立 Track（`bvid#cid`）。
- 搜索结果默认只返回第 1 P。
- `ugc_season`（合集）放 v1.0 之后再评估，不作为 v1 范围。

### 5.8 收藏夹（v0.3，必需）

**接口**

| 用途 | 接口 | 关键参数 |
|---|---|---|
| 收藏夹列表（自建） | `/x/v3/fav/folder/created/list-all` | `up_mid=<mid>` |
| 收藏夹列表（订阅他人） | `/x/v3/fav/folder/collected/list` | `up_mid=<mid>`、`pn`、`ps` |
| 收藏夹内容 | `/x/v3/fav/resource/list` | `media_id`、`pn`、`ps=20`、`platform=web` |

**映射**

- `Playlist.item_id = f"fav:{folder_id}"`，`name = title`，`owner = 登录用户名`，`is_editable = False`。
- 内容项 `medias[]` → `Track`：`bvid`、`title`、`cover`、`duration`、`upper.mid/name`、`fav_time`。
- `attr != 0` 表示稿件失效/私密：跳过并 `logger.debug`，不中断整页。
- `position` 必须按收藏夹顺序递增（跨页连续），否则 MA 排序会乱。

**行为**

- 匿名：`get_library_playlists()` 直接返回（空），不报错。
- 登录但凭据失效：同匿名，warning 一次。
- `mid` 来源：优先 `nav.data.mid`，其次 `DedeUserID` cookie；手填 SESSDATA 时启动后用 `nav` 补齐。
- 分页：`pn` 从 1 开始；响应 `has_more` 为真继续；每页之间 `await throttler.acquire()`。
- 缓存：收藏夹列表/内容 `@use_cache(300)`；`playlist` 详情 `@use_cache(300)`。
- 单夹上限：超过 2000 条时只取前 2000 条并 warning（避免一次同步打爆 API）。

### 5.9 可观测性

- 使用 `self.logger`，INFO 记录「搜索/播放/登录状态变化」，DEBUG 记录请求路径。
- **任何日志都不得包含 `baseUrl`、`SESSDATA`、`bili_jct`**；只允许 host。
- 不引入额外 metrics 依赖。

---

## 6. 工作分解（WBS）

估时单位：人日（熟悉 Python/async 的前提下）。**v1.0 总计 16.5 人日**，含 15% 缓冲约 **19 人日**；PyPI 发布（M8）另加 1 人日。

### M0 — 脚手架（1 d）

| ID | 任务 | 产出 | 依赖 | 估时 | 完成定义 |
|---|---|---|---|---|---|
| T0.1 | 仓库骨架、`manifest.json`、icon、strings | 可加载的空 provider | — | 0.3 | MA 2.10.7 日志出现 provider loaded |
| T0.2 | `compat.py` 能力探测（按 2.10.7 / models 1.1.205） | 版本检查函数 | T0.1 | 0.2 | 旧模型能给出明确错误 |
| T0.3 | CI：ruff / mypy / pytest / pre-commit | 绿色流水线 | T0.1 | 0.5 | PR 必过 |

### M1 — 匿名核心（3 d）

| ID | 任务 | 产出 | 依赖 | 估时 | 完成定义 |
|---|---|---|---|---|---|
| T1.1 | `client.py`：session、header、错误翻译、Throttler | API 客户端 | M0 | 0.8 | 单测覆盖错误映射 |
| T1.2 | WBI 签名 + nav 缓存 + 锁 | `wbi.py` 或 client 内 | T1.1 | 0.5 | 黄金向量测试通过 |
| T1.3 | `search_video` + `parsers`（清洗/时长/去重/打分） | 搜索链路 | T1.2 | 0.8 | fixture 测试 + 真实请求 |
| T1.4 | `view` + `playurl` + 音频候选选择 | 播放链路 | T1.1 | 0.5 | 三种音频形态单测 |
| T1.5 | `search()` / `get_track()` / `get_artist()` / `get_stream_details()` | 可用 provider | T1.3/1.4 | 0.4 | 端到端播放 |
| T1.6 | fixture 录制 + live smoke（marker） | 测试资产 | T1.3/1.4 | 0.3 | CI 默认不联网 |

**M1 验收**：MA 搜「晴天」≥10 条；点播出声；拖动进度；自动下一首；进程不写音频文件。

### M2 — 登录（2 d）

| ID | 任务 | 产出 | 依赖 | 估时 | 完成定义 |
|---|---|---|---|---|---|
| T2.1 | `auth.py`：凭据模型、Cookie、`nav` 校验、降级 | 鉴权层 | M1 | 0.6 | 失效时匿名可用 |
| T2.2 | `setup_flow.py`：二维码、轮询、错误码 | 扫码登录 | T2.1 | 0.8 | 真机扫码成功 |
| T2.3 | 手填 SESSDATA 配置项 | 兜底 | T2.1 | 0.4 | 配置后生效 |
| T2.4 | 登录相关单测 | 测试 | T2.1/2.2 | 0.2 | Cookie/过期/二维码 |

**M2 验收**：扫码成功；重启后保持登录；凭据失效自动降级且日志明确；匿名与登录可切换。

### M3 — 音质与稳定性（1.5 d）

| ID | 任务 | 产出 | 依赖 | 估时 | 完成定义 |
|---|---|---|---|---|---|
| T3.1 | 音质档位与配置生效 | `quality` | M1 | 0.4 | 各档位可选 |
| T3.2 | CDN 探测 + 备份切换 + 健康缓存 | 播放韧性 | M1 | 0.6 | 模拟 403 能切换 |
| T3.3 | 错误分类与提示 | 异常层 | T1.1 | 0.3 | 不整队失败 |
| T3.4 | `max_concurrent_streams` 与并发验证 | 稳定性 | M1 | 0.2 | 3 路并发稳定 |

### M4 — 收藏夹（3 d，必需）

| ID | 任务 | 产出 | 依赖 | 估时 | 完成定义 |
|---|---|---|---|---|---|
| T4.1 | 收藏夹列表/内容 client（`fav/folder/created/list-all`、`fav/resource/list`） | 数据源 | M2 | 1.0 | 分页、总数、权限正确 |
| T4.2 | `get_library_playlists` + `get_playlist` + `get_playlist_tracks` | 资料库 | T4.1 | 1.2 | 收藏夹出现在 MA 播放列表 |
| T4.3 | 去重、失效稿件跳过、`position`、缓存 | 健壮性 | T4.1 | 0.8 | 单条失败不中断整夹 |

**M4 验收**：登录账号的收藏夹全部出现；>100 条的收藏夹能翻页取全；整夹入队可连播；失效稿件跳过且日志可查。

### M5 — 推荐与浏览（2.5 d）

| ID | 任务 | 产出 | 依赖 | 估时 | 完成定义 |
|---|---|---|---|---|---|
| T5.1 | 榜单/新歌/分区榜 client + parser | 数据源 | M1 | 0.8 | 三源均可用 |
| T5.2 | `get_recommendations` / `get_recommendation_items` | 首页三行 | T5.1 | 0.8 | 行内可播 |
| T5.3 | `browse()` 树（榜单/新歌/收藏夹） | 浏览 | T5.1 | 0.6 | 可进可退 |
| T5.4 | 缓存与限流调优 | 稳定性 | T5.1 | 0.3 | 不触发 -412 |

### M6 — 本地开发与分发（1.5 d）

| ID | 任务 | 产出 | 依赖 | 估时 | 完成定义 |
|---|---|---|---|---|---|
| T6.1 | 本地开发回路：bind mount + 重启 + 日志检查脚本 | `make dev` / `scripts/dev.sh` | M1 | 0.5 | 改代码 30 秒内看到效果 |
| T6.2 | Dockerfile 覆盖 + compose override（固定 2.10.7） | Docker 通道 | M1 | 0.5 | 一条命令起 |
| T6.3 | venv 安装/卸载脚本 | 裸机通道 | M1 | 0.3 | 自动定位 site-packages |
| T6.4 | 上游 PR 清单与文档 | 上游准备 | 全部 | 0.2 | checklist 完整 |

### M7 — 测试与文档（2 d）

| ID | 任务 | 产出 | 依赖 | 估时 | 完成定义 |
|---|---|---|---|---|---|
| T7.1 | 单元测试补齐（核心模块 ≥70%） | 测试 | M1–M5 | 0.8 | 覆盖率达标 |
| T7.2 | 集成测试（live marker）+ CI 矩阵（2.10.7 / 2.10.8） | 测试 | M6 | 0.5 | 两档稳定版本都过 |
| T7.3 | README / 安装 / 故障排查 | 文档 | M6 | 0.4 | 新人可照做 |
| T7.4 | 手工矩阵回归 | 报告 | M6 | 0.3 | 三通道通过 |

### M8 — PyPI 发布（1 d，v1.0 之后）

| ID | 任务 | 产出 | 依赖 | 估时 | 完成定义 |
|---|---|---|---|---|---|
| T8.1 | `pyproject.toml`、wheel、`music_assistant.provider` entry point | 可安装包 | v1.0 | 0.4 | `pip install` 后能被 plugin-manager 发现 |
| T8.2 | PyPI Trusted Publishing + 版本 tag | 发布流水线 | T8.1 | 0.3 | 自动出包 |
| T8.3 | plugin-manager 使用文档 | 文档 | T8.1 | 0.3 | Docker/裸机两种示例 |

**M8 触发条件**：v1.0 在本地 Docker 稳定运行 ≥1 周，收藏夹与播放无阻塞问题后再发布，避免把半成品推给用户。

---

## 7. 测试计划

### 7.1 单元测试（默认 CI，不联网）

| 对象 | 用例 |
|---|---|
| WBI | 黄金向量（固定 keys + params + wts → 期望 `w_rid`）；参数排序；特殊字符过滤 |
| 时长解析 | `"4:30"`、`"1:02:03"`、`0`、异常值 |
| 标题清洗 | `<em>`、`【】`、`《》`、全角空格、HTML 实体 |
| 音频选择 | `audio` 数组 / `dolby.audio` 数组 / `flac.audio` 对象 / 全空；档位上限；排序 |
| URL 过期 | `deadline` 解析；无 `deadline` 默认 3600；下限/上限 clamp |
| 凭据 | Cookie 组装；缺字段；过期判断 |
| 错误映射 | `-101/-352/-412/62002/87007` → 对应异常 |
| 去重打分 | 同名不同 UP；播放量差异；时长过滤 |
| 收藏夹解析 | 分页拼装；总数；空收藏夹；失效稿件；`position` 连续 |
| 兼容层 | 2.10.7/2.10.8（单类）与 2.11 dev（mixin）导入同一 provider 模块 |

### 7.2 集成测试（`-m live`，手动/定时）

1. `search("晴天")` → `view` → `playurl` → 对首选 URL 做 Range 请求 → `ffprobe` 校验 `aac/48k/2ch`。
2. 登录态：`nav` → `isLogin=true` → 取 FLAC（有权益账号）。
3. 失效 URL：把 `deadline` 改到过去 → 重新 `get_stream_details` 应拿到新 URL。
4. `-352` 模拟：mock 返回 → 断言 `ResourceTemporarilyUnavailable` 且不缓存。
5. 收藏夹：拉取全部分页 → 断言条数与 B 站页面一致 → 整夹 `get_playlist_tracks` 可播放。

### 7.3 手工矩阵

| 维度 | 取值 |
|---|---|
| 安装方式 | 本地 bind mount / Dockerfile 覆盖 / venv |
| MA 版本 | **2.10.7 / 2.10.8（稳定线）**；2.11 dev 可选 |
| 账号 | 匿名 / 普通登录 / 大会员 |
| 播放器 | 至少 2 种（如 Sonos + 浏览器） |
| 场景 | 搜索、点播、seek、连播、切歌、长暂停后恢复、弱网、收藏夹整夹播放 |

### 7.4 性能与稳定性

- `get_stream_details` p95：不探测 < 300ms；探测 < 800ms。
- `search` p95 < 1.2s。
- 连续播放 30 分钟无中断、无内存增长。
- 100 次搜索不触发 `-412`（限流生效）。

---

## 8. 交付与安装

### 8.1 本地开发回路（当前主用）

目标：改一行代码 → 重启容器 → 30 秒内看到效果。**MA 2.10.8 没有 provider 热重载**，必须重启进程。

```yaml
# docker/compose.dev.yml
services:
  music-assistant:
    image: ghcr.io/music-assistant/server:2.10.8
    environment:
      PYTHONDONTWRITEBYTECODE: "1"   # 只读挂载时避免 __pycache__ 报错
    volumes:
      # 注意：官方镜像把包装在 venv 的 site-packages 里，不是 /app/music_assistant
      - ./provider/bilibili:/app/venv/lib/python3.14/site-packages/music_assistant/providers/bilibili:ro
      - ./data:/data
```

```bash
#!/usr/bin/env bash
# scripts/dev.sh
set -euo pipefail
docker compose -f docker/compose.dev.yml restart music-assistant
docker compose -f docker/compose.dev.yml logs -f --tail=120 music-assistant
```

日常流程：

1. `docker compose -f docker/compose.dev.yml up -d`
2. 改 `provider/bilibili/**`
3. `./scripts/dev.sh`
4. 日志里确认 `Loaded manifest for provider Bilibili` / 无 traceback
5. 在 MA 里搜一首、点播一次

注意：
- 只读挂载（`:ro`）可以跑；`PYTHONDONTWRITEBYTECODE=1` 避免 Python 写 `__pycache__` 失败。
- 改了 `manifest.json`、`strings.json`、`icon.svg` 也必须重启才会重新读取。
- 改 `setup_flow.py` 后，需要删除并重新添加集成实例才会重跑流程。

### 8.2 Dockerfile 覆盖（稳定运行）

```dockerfile
# docker/Dockerfile
ARG MA_VERSION=2.10.8
FROM ghcr.io/music-assistant/server:${MA_VERSION}

COPY provider/bilibili /tmp/bilibili
RUN PROVIDERS="$(/app/venv/bin/python -c \
        'import music_assistant, os; print(os.path.join(os.path.dirname(music_assistant.__file__), "providers"))')" \
    && mkdir -p "$PROVIDERS" \
    && cp -r /tmp/bilibili "$PROVIDERS/bilibili" \
    && rm -rf /tmp/bilibili \
    && /app/venv/bin/uv pip install --no-cache segno==1.6.6
```

```yaml
# docker/compose.build.yml
services:
  music-assistant:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    volumes:
      - ./data:/data
```

要点：
- **不要覆盖 base image 的 ENTRYPOINT/CMD**；MA 需要 `--data-dir/--cache-dir`。
- `MA_VERSION` **固定为 2.10.8**，不用 `latest`；升级时先看 §2.3 的差异表。（`ghcr.io/music-assistant/server:2.10.7` 与 `:2.10.8` 均已确认存在；两版 provider 接口一致，可互换）
- 升级 MA 时重建镜像；provider 代码不变。
- **实测（2.10.8）**：包实际位于 `/app/venv/lib/python3.14/site-packages/music_assistant`，因此 Dockerfile 用 `python -c` 动态定位，不硬编码。

### 8.3 裸机 / venv

```python
# scripts/install_venv.py —— 用 MA 所在 venv 的 python 执行
import pathlib
import shutil

import music_assistant

src = pathlib.Path(__file__).parent.parent / "provider" / "bilibili"
dst = pathlib.Path(music_assistant.__file__).parent / "providers" / "bilibili"
shutil.copytree(src, dst, dirs_exist_ok=True)
print(f"installed to {dst}")
```

```bash
/opt/music-assistant/venv/bin/python scripts/install_venv.py
```

- 必须用 **MA 所在 venv 的 python** 执行；权限不足时用 `sudo -u <ma-user>`，不要直接 `sudo`（会装进 root 的 site-packages）。
- 卸载脚本反向删除 `dst`。
- MA 升级/重装后需重跑（文档写明）。

### 8.4 PyPI + plugin-manager（v1.1，暂缓）

**触发条件**：v1.0 在本地 Docker 稳定运行 ≥1 周，收藏夹与播放无阻塞问题。D6 明确：现在不做。若你希望更早（例如 v0.3 收藏夹跑通后），可把 M8 提前，不影响主线。

发布步骤：
1. `pyproject.toml`：发行名 `bilibili-ma-provider`，包名 `bilibili`，`[project.entry-points."music_assistant.provider"] bilibili = "bilibili"`。
2. wheel 必须包含 `manifest.json`、`strings.json`、`icon.svg`、`setup_flow.py`。
3. PyPI Trusted Publishing（GitHub Actions OIDC），不打 token。
4. 文档同时给 Docker / 裸机 / plugin-manager 三种安装方式。

注意：
- plugin-manager 不是官方组件，需要改启动入口；HAOS add-on 仍不支持。
- 包名冲突风险：安装为顶层 `bilibili`，文档提示先 `pip show bilibili` 检查。

### 8.5 上游 PR（唯一全通道方案）

提交清单：
- `music_assistant/providers/bilibili/`（与本仓库 `provider/bilibili` 同构）
- `manifest.json`：`stage: alpha`、`codeowners`、`documentation`
- `strings.json` + `icon.svg`
- `tests/providers/bilibili/`：单元 + fixture，不依赖外网
- ruff / mypy / pytest 全绿
- PR 描述里说明：非官方 API、只流式播放、不下载、无 DRM 绕过

预期阻力：B 站 ToS 对「提取音轨」不友好，上游可能要求更强的合规表述或拒绝。**因此不能把上游当作 v1 的前提**。

---

## 9. 风险登记册

| ID | 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|---|
| R1 | HAOS add-on 无法注入 provider | 高 | 中 | 明确不支持；上游合并是唯一解 |
| R2 | MA provider API 漂移（2.10.x 单类 → 2.11 beta/dev mixin） | 中 | 高 | 只继承 `MusicProvider`；`compat.py` 能力探测；CI 跑 2.10.7 + 2.10.8，2.11 dev 非阻塞 |
| R3 | 登录触发账号风控 | 中 | 高 | 专用小号、低速率、无写操作、默认匿名 |
| R4 | WBI 算法/密钥变更 | 低 | 高 | 独立模块 + 黄金向量 + 快速发版 |
| R5 | CDN 单点故障 | 中 | 中 | 探测 + 备份切换 + 健康缓存 |
| R6 | 上游 PR 被拒 | 中 | 中 | 插件/挂载通道兜底 |
| R7 | 依赖安装失败（离线环境） | 低 | 低 | v0.1 零依赖；`segno` 失败走手填 |
| R13 | 收藏夹分页/权限/失效稿件 | 中 | 中 | 分页测试 + 单条跳过 + `position` 校验 |
| R8 | 凭据泄露 | 低 | 高 | 不写日志、不进异常、只存 setup_data |
| R9 | 搜索质量差导致「不好用」 | 中 | 高 | 清洗/过滤/打分 + 真实样本回归 |
| R10 | 会员音轨被误当免费 | 低 | 中 | 只读 API 返回的可用流，不构造参数绕过 |
| R11 | 多实例限流叠加 | 低 | 低 | 每实例独立 Throttler；文档说明 |
| R12 | 长时间播放 URL 过期 | 中 | 低 | `expiration` + MA 重取机制 |

---

## 10. 里程碑与排期

| 版本 | 范围 | 累计人日 | 可交付状态 |
|---|---|---|---|
| **v0.1.0-alpha** | M0 + M1 | 4 | 匿名搜索 + 播放 + seek；本地 Docker mount 可用 |
| **v0.2.0-beta** | + M2 + M3 + M6 | ~9 | 扫码登录、音质、CDN 兜底、本地开发回路 + Dockerfile |
| **v0.3.0** | + M4 | ~12 | **收藏夹同步**、整夹播放 |
| **v1.0.0** | + M5 + M7 | ~16.5 | 推荐/浏览、测试与文档完备 |
| **v1.1.0** | + M8 | ~17.5 | PyPI 发布 + plugin-manager 通道 |
| 缓冲 | 15% | ~19 | |

建议节奏：
- 第 1 周出 v0.1（能听）；
- 第 2 周 v0.2（能登录、本地能装）；
- 第 3 周 v0.3（收藏夹，满足 D7）；
- 第 4 周 v1.0（推荐/浏览 + 测试文档）；
- 稳定运行 1 周后再做 v1.1（PyPI）。

---

## 11. 非目标（明确不做）

- ❌ 歌词（D3）：不声明 `LYRICS`，不接 LRCLIB/QQ 歌词；接受 B 站曲目无歌词。
- ❌ UP 主主页 / Artist 作品列表（L1：`-352`）。
- ❌ 下载、离线缓存、转存（D4）。
- ❌ 弹幕、评论、直播、互动视频。
- ❌ 会员/付费音轨绕过。
- ❌ 收藏/投币/关注等写操作（收藏夹只读同步）。
- ❌ 把 B 站音频伪装成本地文件。
- ❌ 复用 BiliMusic 的 Electron 进程。
- ❌ 支持 HAOS add-on（v1 前）。
- ❌ v1.0 前发布 PyPI（D6）。

---

## 12. 待确认

1. **账号类型**：普通账号还是大会员？决定 FLAC/Dolby 能否实测；普通账号也能验证 192k AAC。
2. **收藏夹范围**：只同步「默认收藏夹」，还是所有自建收藏夹？是否包含「稍后再看」？
3. **本地数据目录**：现有 compose 的 `data` 路径与容器名，方便把 `compose.dev.yml` 写成可直接替换的 override。
4. **版本策略**：目标已定为 2.10.8，CI 仍兼容 2.10.7；2.11 beta/dev 只做前瞻。
5. **上游意愿**：如果确定不提 PR，可以省掉 T6.4 与部分上游规范工作。

---

## 附：Definition of Done（每个 PR 通用）

- [ ] `ruff check` / `ruff format --check` 通过
- [ ] `mypy` 通过（provider 目录）
- [ ] 新增逻辑有单测；fixture 不联网
- [ ] 不新增绝对路径、不写盘、不打印敏感信息
- [ ] `get_stream_details` 不缓存 URL；`expiration` 正确
- [ ] 失败路径有明确异常类型与日志
- [ ] 文档/配置项说明同步更新
- [ ] 在 Docker 里手工验证一次播放
