# 把 Bilibili 音频接入 Music Assistant：可行性与方案

> 状态：**调研 + 设计，不含实现**
> 调研对象：`HanversionOvO/BiliMusic`、`music-assistant/server`、`music-assistant/models`（后两者已拉源码逐文件核对）
> 结论口径：只回答「有没有办法、怎么做、坑在哪、要多久」，代码片段均为示意。
>
> **决策更新（2026-10-25）**：目标 **MA 2.10.8**（当前最新稳定，兼容 2.10.7；2.11 仍为 beta/dev 预发布）；安装形态以 Docker 为主、但要求兼容多种安装方式；允许登录 B 站账号；歌词不作为硬需求；**收藏夹必需**；**先本地开发，稳定后再上 PyPI**。
> 据此形成的可执行计划见 [`implementation-plan.md`](./implementation-plan.md)，本文作为其技术依据保留。

---

## 0. 结论先行

**可行，而且不需要改 Music Assistant 内核。**

推荐做法：写一个 **原生 Music Provider（Python，跑在 MA 服务端）**，直接消费 B 站视频的 DASH 音轨地址，把音频 URL 放进 `StreamDetails`，并通过 `extra_input_args` 给 ffmpeg 注入 `Referer` / `User-Agent`，由 MA 现有的解码与缓冲链路负责播放。

真正的难点不在「能不能播」，而在三件事：

1. **反盗链**：音频 CDN 对 `Referer` 敏感，必须把请求头一路带到 ffmpeg（已实测验证）。
2. **风控**：搜索/空间接口需要 WBI 签名，部分接口对「非浏览器指纹」直接返回 `-352`（已实测撞到）。
3. **「视频 ≠ 歌曲」**：标题噪声、翻唱/一小时循环/教程混在一起，元数据质量决定这个 provider 好不好用。

工作量（单人、熟悉 MA provider 的前提下）：

| 阶段 | 内容 | 预估 |
|---|---|---|
| P0 MVP | 匿名搜索 + 播放 + seek | 3–5 人日 |
| P1 可用 | 扫码登录、音质、榜单推荐、歌词 | +5–7 人日 |
| P2 资料库 | 收藏夹/合集同步、UP 主页 | +5–8 人日 |

不建议把 BiliMusic 的 Electron 进程当作「服务端」复用：它是客户端，代码是 TypeScript + 渲染进程 Cookie + Electron `net`，MA 这边一行都调不到。**能复用的是接口知识和算法（WBI、标题清洗、歌词匹配），不是代码。**

---

## 1. 两个项目分别是什么

### 1.1 BiliMusic：一个 Electron 桌面客户端

它把 B 站当音乐库，核心价值在「产品层」：Apple Music 风格 UI、队列、歌单、歌词、托盘。

与接入 MA 相关的部分：

| BiliMusic 位置 | 能力 | 对 provider 的价值 |
|---|---|---|
| `src/services/bilibiliApi.ts` | WBI 签名、搜索、UP 主、详情、`playurl`、音频流选择、收藏夹、音乐中心榜单 | **接口清单与参数可直接照抄** |
| `electron/biliApi.ts` | 主进程代理、二维码登录、下载 | 登录流程可参考；下载能力与 MA 流式播放策略冲突，不采用 |
| `src/services/lyrics.ts` | 标题清洗（噪声词表、书名号/引号提取）、候选打分、歌词缓存 | **算法可移植**，是 B 站曲目能不能配上歌词的关键 |
| `src/contexts/PlayerContext.tsx` | 播放队列与状态 | 与 MA 队列重复，不需要 |

关键接口（均已在 BiliMusic 中封装）：

- 搜索：`GET /x/web-interface/wbi/search/type?search_type=video&keyword=...`（**需 WBI 签名**）
- 详情：`GET /x/web-interface/view?bvid=...` → `cid` / `pages` / `owner` / `pic` / `duration`
- 播放地址：`GET /x/player/playurl?bvid=&cid=&qn=0&fnver=0&fnval=16&fourk=1` → `dash.audio[]`
- UP 主投稿：`GET /x/space/wbi/arc/search`（**需 WBI，且实测被风控**）
- 音乐中心：`/x/centralization/interface/music/comprehensive/web/rank`、`/x/centralization/interface/new/music`
- 分区榜：`/x/web-interface/ranking/region?rid=3&day=3`
- 二维码登录：`passport.bilibili.com/x/passport-login/web/qrcode/{generate,poll}`
- 收藏夹：`/x/v3/fav/folder/created/list-all?up_mid=...`（列表项接口 BiliMusic 未封装）

> 注：B 站早期的独立音频区（`auXXXX`）已基本退出主流程，现在没有面向第三方的音乐 API。所谓「B 站音频」实际就是**视频的 DASH 音轨**，这也是 BiliMusic 的做法。

### 1.2 Music Assistant：provider 是服务端 Python 插件

MA 2.10.7 有 128 个 provider（HEAD 约 142 个），**没有 bilibili**，但有几个高度可参考的同类：

- `nicovideo`：视频站当音乐源，`video → Track` 的映射与 provider mapping 写法
- `qqmusic`：国内服务、Cookie/扫码鉴权、直链播放、歌词
- `neteasecloudmusic`：非官方 API、Cookie 鉴权

Provider 的最小契约（源码位置 `music_assistant/models/`）：

| 方法 / 属性 | 必要性 | 说明 |
|---|---|---|
| `setup(mass, manifest, config)` | 必需 | 返回 provider 实例 |
| `get_stream_details(item_id, media_type)` | **必需** | 播放的核心；返回 `StreamDetails` |
| `get_audio_stream(...)` | 可选 | 仅 `StreamType.CUSTOM` 时需要 |
| `search(query, media_types, limit)` | 声明 `SEARCH` 后必需 | 返回 `SearchResults` |
| `get_track(item_id)` | 基本必需 | 详情、队列解析、歌词入口 |
| `browse(path)` | 声明 `BROWSE` 后必需 | 树形浏览 |
| `get_recommendations()` / `get_recommendation_items()` | 声明 `RECOMMENDATIONS` 后必需 | 首页推荐行 |
| `get_library_*` / `get_library_playlists` | 声明 `LIBRARY_*` 后必需 | 定时同步进 MA 资料库 |
| `get_config_entries()` | 可选 | 质量、过滤等配置项 |
| `handle_async_init()` / `unload()` | 可选 | 建会话、释放资源 |
| `setup_flow.py` | 可选 | 扫码/OAuth 等一次性登录（存在即 `has_setup_flow=true`） |

MA 的播放是**两段式**：

1. `get_stream_details()` 只负责「怎么拿到音频」——URL、格式、能否 seek、有效期；
2. 真正拉流由 MA 的 stream controller 调用 ffmpeg 完成（`StreamType.CUSTOM` 才回调 provider 拿字节）。

`StreamDetails` 里有一个关键字段 `extra_input_args`：provider 可以塞任意 ffmpeg 输入参数，**位置在 `-i` 之前**（HEAD `helpers/ffmpeg.py:640`；2.10.7 为 `:545`）。这正是注入 `-headers` 的官方入口。HEAD 还会在 MP3 seek 探测时复用这些 header（`helpers/mp3.py:84`），2.10.7 没有该 helper，但 m4s/AAC 不受影响。

分发方式（三种，详见 §5）：核心 PR、第三方插件包 + `music-assistant-plugin-manager`、直接挂载进容器。

---

## 2. 可行性实测记录

不是纸面推演，下面每条都在当前环境跑过（2026-10，非国内 IP）。

| # | 验证项 | 方法 | 结果 | 对方案的影响 |
|---|---|---|---|---|
| 1 | `playurl` 匿名可用性 | 用公开 MV（`BV1GJ411x7h7`）请求 `fnval=16` | `code 0`，返回 3 条音轨：`30216`(≈44k) / `30232`(≈103k) / `30280`(≈204k)，均 AAC 48kHz 立体声 | **不登录也能拿到 192k AAC**，登录是增强而非前提 |
| 2 | CDN 反盗链 | 对同一 `playurl` 的主链与备份链分别 `curl -r 0-1023` | 主链 `*.mcdn.bilivideo.cn` → `206`（不校验）；备份链 `*.bilivideo.com` → **`403`** | 必须带 `Referer`，否则一旦主链不可用就播不了 |
| 3 | ffmpeg 是否认这套 header | 对备份链跑 `ffmpeg -i`，对比带/不带 `-headers` | 不带 → `Server returned 403 Forbidden`；带 `-headers "Referer: ...\r\nUser-Agent: ..."` → 正常解出 `aac / 48000 / 2ch` | **`StreamDetails.extra_input_args=["-headers", ...]` 方案成立** |
| 4 | seek | `ffmpeg -ss 60` + 同样 header | 成功输出 3s 音频 | 直链支持 Range，`can_seek=True` 可用 |
| 5 | WBI 搜索 | Python 里按 BiliMusic 的置换表实现签名，请求 `wbi/search/type` | `code 0`，`numResults=1000`；标题含 `<em class="keyword">` 标签，时长为 `"4:30"` 字符串，封面为 `//i0.hdslb.com/...` | 搜索可服务端实现；**必须清洗 HTML 标签、补协议、解析时长** |
| 6 | UP 主空间接口 | 同一套 WBI 签名请求 `space/wbi/arc/search`，并分别加 `buvid3/buvid4`、`Origin`、`Sec-Fetch-*` | **始终 `-352 风控校验失败`** | **Artist 作品列表功能不可靠**，要按「优雅降级」设计 |
| 7 | 榜单/推荐接口 | 请求综合榜、新歌、分区榜、首页推荐 | 全部 `code 0`，数据正常 | 推荐行/浏览树低风险，适合 P1 |
| 8 | MA 流路径是否有额外预检 | 读 `controllers/streams/audio.py` 播放路径 | 无 HEAD/GET 预检，直接把 URL 交给 ffmpeg；`extra_input_args` 全程透传 | 不需要为「MA 自己去拉 URL」做额外适配 |
| 9 | 流地址过期后的行为 | 读 `audio.py:3691` | `created_at + expiration` 到期会**重新调用** `get_stream_details` | 用 `expiration` 控制 B 站签名 URL 的生命周期 |
| 10 | ffmpeg 输入参数从哪来 | 读 `helpers/ffmpeg.py:640-690` | `extra_input_args` 在 `-i` 前；**只有 PCM 才会自动补 `-f`**，非 PCM 只补 `-acodec` | fMP4 分片靠 ffmpeg 自己探测，实测没问题 |
| 11 | 图片是否防盗链 | 直接 `curl` 封面 | `200 image/jpeg`，不需要 Referer | 封面可远程引用，无需代理 |
| 12 | MA 自带歌词兜底是否够用 | 读 `providers/lrclib/__init__.py` | LRCLIB `/get` 需要 **track_name + artist_name + duration 精确匹配**；而 B 站曲目的 artist 是 UP 主、name 是视频标题 | **兜底基本不会命中**，想要歌词必须自己实现 |

一句话：**播放链路已经打通，剩下的都是工程与产品问题。**

---

## 3. 方案设计

### 3.1 总体架构

```
┌────────────────────────── Music Assistant ──────────────────────────┐
│  search / get_track                                                 │
│      │                                                              │
│      ▼                                                              │
│  providers/bilibili  (Python, async)                                │
│   ├─ client.py      WBI 签名 · aiohttp · Throttler · use_cache      │
│   ├─ parsers.py     video / music-center → Track / Artist           │
│   ├─ lyrics.py      标题清洗 + 歌词匹配（P1）                        │
│   └─ setup_flow.py  扫码登录（P1）                                   │
│      │                                                              │
│      │  StreamDetails(path=<CDN m4s>, extra_input_args=["-headers",…])│
│      ▼                                                              │
│  stream controller ──► ffmpeg ──HTTPS(Referer/UA)──► bilivideo CDN   │
│      │                                                              │
│      └─► AudioBuffer ─► 音量归一化/淡入淡出 ─► 各播放器              │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 建议目录结构

```text
music_assistant/providers/bilibili/
├── __init__.py        # setup() / SUPPORTED_FEATURES / BilibiliProvider
├── manifest.json      # type=music, domain=bilibili, stage=alpha
├── constants.py       # 域名、音质档位、噪声词表、限流参数
├── client.py          # WBI、API 封装、错误翻译、节流、缓存
├── parsers.py         # API dict -> Track / Artist / Playlist
├── lyrics.py          # 标题清洗 + 歌词匹配（P1）
├── setup_flow.py      # 二维码登录（P1）
├── strings.json       # 文案/翻译
└── icon.svg
```

依赖尽量少：`aiohttp` 用 MA 自带的 `mass.http_session`；只有二维码渲染需要额外包（`qrcode[pil]`，P1 再加，manifest 里**锁版本**）。

### 3.3 数据映射

| Bilibili | MA 模型 | 说明 |
|---|---|---|
| `bvid` | `Track.item_id` | 稳定、可读、可拼 URL |
| `title` | `Track.name` | 必须去 `<em>`、去【】噪声、trim |
| `owner.name` / `owner.mid` | `Track.artists`（`Artist` 或 `ItemMapping`） | 语义是「UP 主」而非「歌手」，见 §4 |
| `pic` | `metadata.images`（`ImageType.THUMB`） | `//` 开头要补 `https:` |
| `duration` | `Track.duration` | 搜索返回 `"4:30"`，要解析 |
| `stat.view` | `metadata.popularity` | 用于排序与展示 |
| `pubdate` | `metadata.release_date` | |
| `tag` | `metadata.genres` | 逗号分隔 |
| 视频页 URL | `metadata.links`（`LinkType.WEBSITE`） | 点开回 B 站 |
| `cid` | `ProviderMapping.details` 或 item_id 后缀 | 播放必需 |

示意（构造 Track）：

```python
from music_assistant_models.enums import ImageType, LinkType
from music_assistant_models.media_items import (
    Artist, ItemMapping, MediaItemImage, MediaItemLink, MediaItemMetadata,
    ProviderMapping, Track, UniqueList,
)

Track(
    item_id=raw["bvid"],
    provider=self.instance_id,
    name=clean_title(raw["title"]),
    duration=parse_duration(raw["duration"]),
    artists=UniqueList([
        ItemMapping(item_id=str(raw["mid"]), provider=self.instance_id, name=raw["author"])
    ]),
    metadata=MediaItemMetadata(
        images=UniqueList([
            MediaItemImage(
                type=ImageType.THUMB,
                path=to_https(raw["pic"]),
                provider=self.instance_id,
                remotely_accessible=True,
            )
        ]),
        links={MediaItemLink(
            type=LinkType.WEBSITE,
            url=f"https://www.bilibili.com/video/{raw['bvid']}",
        )},
        popularity=raw.get("play"),
    ),
    provider_mappings={
        ProviderMapping(
            item_id=raw["bvid"],
            provider_domain=self.domain,
            provider_instance=self.instance_id,
            available=True,
            url=f"https://www.bilibili.com/video/{raw['bvid']}",
        )
    },
)
```

### 3.4 `item_id` 设计（容易踩坑的地方）

- 普通单 P 稿件：`item_id = bvid`。搜索接口只给 `bvid`，不给 `cid`；如果坚持「id 里必须带 cid」，一次搜索就要多发 20 次 `view` 请求，不可接受。
- 多 P 稿件 / 音乐中心条目：`item_id = f"{bvid}#{cid}"`（音乐中心可能只有 `aid+cid`，用 `f"av{aid}#{cid}"`）。
- 播放时按 `#` 拆分；没有 `cid` 就调 `view` 拿 `pages[0].cid`，`view` 结果 `@use_cache(24h)`。
- `get_track()` 对无 `cid` 的 id 返回完整详情；对带 `cid` 的返回对应分 P 的标题。

这样既避免搜索放大请求，又能支持合集/多 P。

### 3.5 鉴权

| 模式 | 能力 | 建议 |
|---|---|---|
| 匿名 | 搜索、详情、`playurl`（实测 192k AAC）、榜单 | **P0 默认**，零门槛 |
| SESSDATA（扫码或手填） | 更高码率/会员音轨、收藏夹、稍后再看、部分风控放宽 | P1 可选 |
| 完整 Cookie（`SESSDATA` + `bili_jct` + `DedeUserID` + `buvid3`） | 写操作（收藏）、更稳的风控画像 | 仅 P2 需要，且默认不做写操作 |

登录实现（照 `qqmusic/setup_flow.py` 的成熟模式）：

1. `GET passport.bilibili.com/x/passport-login/web/qrcode/generate` → `{url, qrcode_key}`
2. 服务端用 `qrcode` 渲染成 data-URI，`session.progress_until(..., image=..., expires_in=180)`
3. 轮询 `.../qrcode/poll?qrcode_key=`：`86101` 未扫、`86090` 已扫待确认、`0` 成功、`86038` 过期
4. 从响应的 `Set-Cookie` 取凭据，`session.finish({...})` 持久化
5. provider 侧用 `self.get_setup_value("credential_json")` 读取

**风险提示**：服务端 IP 登录 B 站账号有触发风控/限制的可能。建议文档里明确「用一个不重要的账号」「默认匿名」，并把登录做成可选。

### 3.6 搜索

流程：

```
keyword
  → WBI 签名（nav 取 img_key/sub_key，缓存 6h）
  → /x/web-interface/wbi/search/type?search_type=video&page=1&page_size=20
  → 清洗 / 过滤 / 去重 / 打分
  → SearchResults(tracks=[...])
```

签名要点（实测通过）：

```python
MIXIN_KEY_ENC_TAB = [46, 47, 18, 2, 53, 8, 23, 32, ...]  # 共 64 项，照抄 BiliMusic

def sign(params: dict[str, str | int], img_key: str, sub_key: str) -> str:
    mixin_key = "".join((img_key + sub_key)[i] for i in MIXIN_KEY_ENC_TAB)[:32]
    params = {**params, "wts": int(time.time())}
    query = "&".join(
        f"{quote(k, safe='')}={quote(str(v).translate(STRIP), safe='')}"
        for k, v in sorted(params.items())
    )
    return f"{query}&w_rid={md5((query + mixin_key).encode()).hexdigest()}"
```

- `STRIP` 去掉 `!'()*`（B 站要求），`quote` 用同一套规则；
- **签名后的 query 必须原样发出**，不能再被 URL 库二次编码；
- `img_key/sub_key` 每日轮换，来自 `/x/web-interface/nav`（未登录也返回），缓存 6h。

结果清洗（决定「能不能用」的关键）：

1. 去 HTML：`<em class="keyword">晴天</em>` → `晴天`；
2. 去噪声：`【Hi-Res无损音质】`、`【4K修复】`、`官方MV`、`完整版`、`动态歌词` 等（直接移植 BiliMusic 的 `NOISE` / `CJK_NOISE` 词表）；
3. 时长过滤：默认 `60s ~ 15min`，挡掉一小时循环、直播回放、课程；
4. 去重：`(clean_title, author)` 归一化后保留播放量最高的；
5. 打分：关键词命中 > 标题干净度 > 播放量；
6. 截断到 `limit`。

可选：`search_type=video` 之外再查一次 `search_type=bili_user`，映射成 `Artist`（低优先级）。

### 3.7 播放（核心）

```python
async def get_stream_details(self, item_id: str, media_type: MediaType) -> StreamDetails:
    bvid, cid = await self._resolve_ids(item_id)          # view 结果走缓存
    track = await self.get_track(item_id)                  # 内部同样命中 view 缓存
    data = await self._api.playurl(bvid, cid, qn=0, fnval=16, fourk=1)
    stream = self._pick_audio(data["dash"]["audio"])       # 见下表
    # 签名 URL 自带 deadline（unix 秒），用它决定 streamdetails 的有效期
    deadline = parse_qs(urlparse(stream["baseUrl"]).query).get("deadline", [None])[0]

    return StreamDetails(
        provider=self.instance_id,
        item_id=item_id,
        media_type=MediaType.TRACK,
        stream_type=StreamType.HTTP,
        path=stream["baseUrl"],
        audio_format=AudioFormat(
            content_type=ContentType.MP4,
            codec_type=ContentType.AAC,
            bit_rate=stream["bandwidth"] // 1000,
        ),
        duration=track.duration,
        can_seek=True,
        allow_seek=True,
        expiration=_expiry(deadline),   # 取 min(URL 截止, 3600) - 60s，下限 300
        extra_input_args=[
            "-headers",
            "Referer: https://www.bilibili.com\r\n"
            "Origin: https://www.bilibili.com\r\n"
            "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36\r\n",
        ],
    )
```

音质档位（按 `id` 选，按配置上限截断）：

| id | 规格 | 备注 |
|---|---|---|
| `30280` | 192k AAC（实测带宽 ≈204 kbps） | 默认首选 |
| `30232` | 132k AAC（实测 ≈103 kbps） | fallback |
| `30216` | 64k 档（实测 ≈44 kbps） | 兜底 |
| `30250` | Dolby Atmos | 需会员；不主动绕过 |
| `30251` | Hi-Res FLAC | 需会员；同上 |

工程细节：

- **不要缓存音频 URL**：签名 URL 与 IP/时间绑定，只在播放瞬间取；`playurl` 本身不缓存。
- **CDN 选择**：`baseUrl` 常是 `*.mcdn.bilivideo.cn`（P2P 镜像，国内快），`backupUrl` 是 `upos`/`bilivideo.com`（必须带 Referer）。
- **失败自愈**：MA 在流错误时复用同一份 `StreamDetails` 重试，**不会重新调用 provider**。所以如果实测发现单 CDN 不稳定，有两条路：
  - 低成本：返回前对 `baseUrl` 做一次 `Range: bytes=0-1023` 探测（~50ms），失败换 `backupUrl`，按 host 缓存健康状态；
  - 彻底：改 `StreamType.CUSTOM` + 自己实现多 CDN 重试与 Range seek（多 2–3 人日）。
  建议 P0 先用「主链 + 轻量探测」，有数据再升级。
- **多 P**：只播目标 `cid`；`view.pages` 里每 P 可单独成 Track。
- **不可播**：`dash.audio` 为空（充电专属/预告/互动视频）→ 抛 `UnplayableMediaError`，让 MA 正常跳过而不是整队失败。
- **合规**：`StreamDetails.path` 序列化时被丢弃（server-only），不会泄漏给客户端；不落盘、不缓存解码后的音频，符合 MA 的流式 provider 政策。

### 3.8 推荐与浏览（P1）

| MA 能力 | 数据源 | 备注 |
|---|---|---|
| `RECOMMENDATIONS` 行 | 音乐综合榜、新歌速递 | 实测可用，元数据比搜索干净（有 `music_title` / `author` / `album`） |
| 推荐行 | 分区榜（`rid=3` 音乐区） | |
| 推荐行 | 首页推荐 `index/top/rcmd` | 噪声大，需强过滤，可后置 |
| `BROWSE` 树 | 榜单 / 新歌 / 收藏夹 / 稍后再看 | 收藏夹需登录 |
| `LIBRARY_PLAYLISTS` | 收藏夹（`/x/v3/fav/resource/list`） | 只读；不做增删，避免写操作与 csrf |

音乐中心的坑：综合榜的 `bvid` 可能是失效稿件，要取 `related_archive.bvid`，或直接用 `avid + cid` 走 `playurl` 回退——BiliMusic 已经处理过这个 case（`extractAudioFromVideo` 的 fallback 分支）。

### 3.9 歌词（P1，且必须自己做）

这是最容易被低估的一块。

MA 的歌词查找顺序是：**曲目自身 provider（若声明 `LYRICS`）→ 其它 metadata provider（lrclib / genius）**。
而 LRCLIB 走的是 `/get`，要求 `track_name`、`artist_name`、`duration` 精确匹配（±2s）。B 站曲目：

- `name` 是「【Hi-Res无损】《晴天》- 周杰伦 - 故事的小黄花」
- `artist` 是 UP 主「VV音乐局」，不是周杰伦

**结论：不实现 `LYRICS`，就基本没有歌词。**

建议实现：

1. 复用 BiliMusic 的标题清洗：去噪声词、提取《》/「」内候选、拆 `歌手 - 歌名`；
2. 用「清洗后的歌名 + 候选歌手 + 时长」调 LRCLIB 的 `/search`（模糊接口，比 `/get` 宽容），按标题相似度/歌手/时长打分；
3. 命中后写 `track.metadata.lrc_lyrics`，`@use_cache(7d)` 缓存，失败也缓存（避免每次重试打爆）；
4. 备选：BiliMusic 用的 QQ 音乐歌词 API（OIAPI）。命中率更高，但属于第三方非官方接口，**默认关闭**，作为可选项并写清合规风险。

### 3.10 配置项

| key | 类型 | 默认 | 用途 |
|---|---|---|---|
| `quality` | select | `auto`（最高可用） | 上限档位：`auto` / `30280` / `30232` / `30216` |
| `min_duration` | int | 60 | 过滤短视频 |
| `max_duration` | int | 900 | 过滤长循环/直播回放 |
| `search_limit` | int | 20 | 单次搜索取多少条 |
| `enable_lyrics` | bool | true | 是否走歌词匹配 |
| `lyrics_source` | select | `lrclib` | `lrclib` / `qq`（需明示风险） |
| `credential_json` | secure（setup_data） | 空 | 登录凭据；空 = 匿名 |
| `unofficial_provider_note` | alert | — | MA 现成的「非官方 provider」提示 |

---

## 4. 风险与对策

| 风险 | 触发条件 | 影响 | 对策 |
|---|---|---|---|
| CDN 403 | 备份链不带 `Referer` | 播放失败 | `extra_input_args` 注入 header；主链失败切备份 |
| WBI 过期 | `img_key/sub_key` 每日轮换 | 搜索/空间接口报错 | 缓存 6h，失败自动刷新一次 |
| `-412` 风控 | 高频、异常 UA、机房 IP | 搜索被拒 | `Throttler`（建议 3 req/s，搜索 1 req/s）+ 真实 UA + 退避 |
| `-352` 风控 | UP 主空间接口，实测即使带 `buvid3/4` 仍失败 | Artist 作品列表不可用 | **不声明 `ARTIST_TRACKS`**，Artist 只作为展示/跳转；需要时降级为「按 UP 主名搜索」 |
| URL 过期 | 签名 `deadline` 约 2h | 长时间暂停后播放失败 | `expiration` 设为 URL 剩余寿命；MA 到期自动重新取 |
| 多 P / 合集 | 一个 bvid 多个 cid | 播错分 P | `item_id` 带 `#cid`；`view.pages` 展开 |
| 付费/会员音轨 | 30250/30251 | 取不到或 403 | 不绕过；按账号实际可用档位降级 |
| 地区限制 | 部分稿件仅限中国大陆 | 无法播放 | 明确报错并跳过；文档说明服务器位置影响 |
| 搜索结果噪声 | 视频站本质 | 搜「晴天」出来教程/一小时循环 | 时长过滤 + 噪声词 + 去重 + 打分 |
| 账号风控 | 服务端 IP 登录/高频 | 账号受限 | 默认匿名；登录可选；低速率；文档警示 |
| 版权/ToS | B 站禁止下载/二次分发 | 上游审核风险 | 只流式播放、不落盘、不提供下载；标注非官方与个人使用 |
| CDN 单点 | P2P 镜像不稳定 | 播放中断 | 见 §3.7 的探测/自定义流方案 |
| 上游不接受 | MA 对非官方 API 审核趋严 | PR 被拒 | 走插件包分发（§5） |

关于「UP 主 = 歌手」的语义问题：MA 的 `Artist` 页面天然会想要「该艺人的所有曲目」。B 站上同一个歌手分散在无数 UP 主稿件里，把 UP 主当 Artist 会出现「周杰伦（XX音乐站）」这种奇怪实体。建议 P0/P1 就把 Artist 当「上传者」展示，不承诺聚合；真要聚合得引入外部音乐库做实体对齐，属于另一个项目。

---

## 5. 落地路径对比

| 路径 | 做法 | 优点 | 缺点 | 适用 |
|---|---|---|---|---|
| **A. 核心 PR** | 直接往 `music_assistant/server` 加 `providers/bilibili/` | 用户零配置、随版本升级、有 CI/评审 | 审核周期长；非官方 API + 版权敏感，**通过与否不确定** | 想长期维护、面向所有用户 |
| **B. 独立插件包** | 打成 pip 包，注册 `music_assistant.provider` entry point，配合 [`music-assistant-plugin-manager`](https://github.com/TigreGotico/music-assistant-plugin-manager) 启动 | 不碰内核、迭代快、可先验证体验 | 用户要换启动方式/改 Dockerfile；依赖第三方补丁 | **推荐先走这条** |
| **C. 目录挂载** | 把 provider 目录挂进容器 `<site-packages>/music_assistant/providers/bilibili`（2.10.8 为 `/app/venv/lib/python3.14/site-packages/...`） | 最简单，个人自用足够 | 镜像升级易丢；无版本管理 | 个人折腾/快速验证 |
| **D. OpenSubsonic 桥** | 把 B 站逻辑做成一个 Subsonic 服务，MA 用现成 `opensubsonic` provider 接 | 完全不写 MA 代码，顺带获得资料库/歌单语义 | 要另写一个服务 + 实现一堆 Subsonic 端点；多一层进程；体验割裂 | 已有服务端框架时 |
| **E. RSS/Podcast PoC** | 起个小服务，把歌单生成 RSS（enclosure 指向本地代理，代理补 Referer） | 半天到一天就能听到声 | 播客 UI、无音乐队列语义、无搜索 | 只想先验证「能不能播」 |

**建议**：按 B 起步，代码结构按 A 的标准写（不 import 私有模块、加测试、ruff/mypy 干净），验证 2–3 周后再决定是否提 PR。

---

## 6. 分期计划

> 注：本节为初版排期，已被 [`implementation-plan.md`](./implementation-plan.md) 的 M0–M8 取代（歌词移除、收藏夹前置、PyPI 延后）。保留仅作历史参考。

### P0 — MVP（3–5 人日）

- `manifest.json` + provider 骨架 + 配置项
- `client.py`：WBI、`nav`、`view`、`playurl`、错误翻译、`Throttler`
- `search()`：清洗/过滤/去重/打分
- `get_track()` + `get_stream_details()`（含 header、音质、expiration）
- 单测：WBI 参数排序与签名、时长/标题解析、搜索结果 fixture
- **验收**：MA 里搜「晴天」出结果；点播能出声、能拖动进度、能自动下一首；进程不写音频文件；连续播 30 分钟不中断。

### P1 — 可用（+5–7 人日）

- 扫码登录 setup flow（含「匿名跳过」）+ 手填 SESSDATA 兜底
- 推荐行（综合榜/新歌/分区榜）+ browse 树
- 歌词（清洗 + LRCLIB `/search` 打分 + 缓存）
- 错误分类：`-101/-352/-412/404/无音轨` → 对应 MA 异常
- CDN 探测与备份切换
- **验收**：登录后按账号权益自动选到更高档位（无权益则仍为 192k，不报错）；榜单可整行播放；抽样 50 首歌词命中 ≥60%；风控错误不导致 provider 整体不可用。

### P2 — 资料库（+5–8 人日）

- 收藏夹 → `Playlist`（只读），`LIBRARY_PLAYLISTS`
- 多 P/合集展开
- Artist 页 best-effort（失败降级）
- 可选：稍后再看、历史
- **验收**：收藏夹出现在 MA 的播放列表；整夹入队可连播；同步失败不影响搜索/播放。

---

## 7. 非目标

- ❌ 下载/离线缓存音频（违反 MA 流式 provider 政策，也违反 B 站 ToS）
- ❌ 弹幕、评论、直播流、互动视频
- ❌ 绕过会员/付费音轨
- ❌ 写操作（收藏、投币、关注）
- ❌ 把 B 站音频伪装成「本地文件」塞进资料库
- ❌ 复用 BiliMusic 的 Electron 进程做代理

---

## 8. 待确认（需要你拍板）

> 已确认：MA 跑在 Docker 且要求多安装方式兼容；允许登录；歌词非必需；**收藏夹必需**；**先本地开发、后上 PyPI**；目标版本 **MA 2.10.7**。剩余问题见 [`implementation-plan.md`](./implementation-plan.md) §12。

1. **部署形态**：MA 是 Docker、HA add-on 还是裸机？这直接决定走 B 还是 C。
2. **是否接受登录**：匿名够用（192k），但收藏夹/更高音质需要登录；能否接受一个专用小号的风控风险？
3. **歌词是不是硬需求**：是的话 P1 必须排进去，且要接受「非官方歌词源」的合规取舍。
4. **要不要 UP 主页/Artist 聚合**：实测该接口被风控，建议先不做。
5. **是否愿意长期维护并提 PR**：愿意的话，代码从一开始就按上游标准写。
6. **服务器位置**：非国内 IP 在 CDN 速度、部分稿件可用性上会有差异，需要实测你那边的情况。

---

## 附录 A：接口清单（provider 需要实现的）

| 用途 | 接口 | 鉴权 | WBI | 备注 |
|---|---|---|---|---|
| WBI keys | `/x/web-interface/nav` | 否 | 否 | 未登录也返回 `wbi_img` |
| 搜索 | `/x/web-interface/wbi/search/type` | 否 | **是** | `search_type=video` |
| 用户搜索 | 同上 | 否 | 是 | `search_type=bili_user` |
| 详情 | `/x/web-interface/view` | 否 | 否 | 给 `cid`/`pages`/`owner` |
| 播放地址 | `/x/player/playurl` | 否（登录更佳） | 否 | `fnval=16` |
| 分区榜 | `/x/web-interface/ranking/region` | 否 | 否 | `rid=3` |
| 综合榜 | `/x/centralization/interface/music/comprehensive/web/rank` | 否 | 否 | |
| 新歌 | `/x/centralization/interface/new/music` | 否 | 否 | |
| 首页推荐 | `/x/web-interface/index/top/rcmd` | 否 | 否 | 噪声大 |
| 收藏夹列表 | `/x/v3/fav/folder/created/list-all` | **是** | 否 | `up_mid` |
| 收藏夹内容 | `/x/v3/fav/resource/list` | **是** | 否 | 分页 |
| UP 主投稿 | `/x/space/wbi/arc/search` | 建议登录 | 是 | **实测匿名 -352，慎用** |
| 二维码 | `passport.../qrcode/generate` / `poll` | 否 | 否 | 登录用 |
| 指纹 | `/x/frontend/finger/spi` | 否 | 否 | `buvid3/buvid4` |

## 附录 B：代码坐标（方便实现时对照）

**Music Assistant**

> 注：下文代码坐标基于 server **HEAD（2.11 beta/dev 预发布）**。当前目标 **MA 2.10.7 / 2.10.8（稳定线）** 的差异：`models/media_capabilities.py` 不存在（`MusicProvider` 为单类）、无 `helpers/mp3.py`、`extra_input_args` 透传在 `audio.py:3848`、`ffmpeg` 参数拼装在 `ffmpeg.py:545`、过期重取在 `audio.py:3340`。详细兼容策略见 [`implementation-plan.md`](./implementation-plan.md) §2.3。

- provider 基类：`music_assistant/models/music_provider.py`（`MusicProvider`）
- 能力 mixin（仅 2.11 beta/dev）：`music_assistant/models/media_capabilities.py`（`AudioStreamMixin` / `MediaCatalogMixin` / `RecommendationsMixin`）
- 播放起点：`music_assistant/controllers/streams/audio.py:4549`（`extra_input_args` 透传）
- ffmpeg 参数拼装：`music_assistant/helpers/ffmpeg.py:612-700`
- header 复用探测（仅 2.11 beta/dev）：`music_assistant/helpers/mp3.py:84`（`ffmpeg_http_headers`）
- 过期重取：`music_assistant/controllers/streams/audio.py:3691`
- 扫码登录范例：`music_assistant/providers/qqmusic/setup_flow.py`
- 视频转 Track 范例：`music_assistant/providers/nicovideo/converters/track.py`
- 歌词兜底：`music_assistant/providers/lrclib/__init__.py`
- 未实现模板：`music_assistant/providers/_demo_music_provider/`

**BiliMusic**

- WBI 与接口：`src/services/bilibiliApi.ts`（`getMixinKey` / `encodeWbi` / `getPlayUrl` / `extractAudioFromVideo`）
- 登录：`electron/biliApi.ts`（`bili:qrGenerate` / `bili:qrPoll`）
- 标题清洗与歌词：`src/services/lyrics.ts`（`NOISE` / `CJK_NOISE` / 候选打分）
- 音频选择：`src/services/bilibiliApi.ts`（`getBestAudioUrl`）

---

## 附：一句话给决策者

> 技术上已经验证到「ffmpeg 带 Referer 能稳定解出 B 站 192k AAC 并能 seek」，MA 的 provider 接口也天然支持传 header；剩下的工作量主要是把「视频搜索」洗成「歌曲搜索」，以及处理风控与歌词。建议先用独立插件包做 P0，2 周内就能听到声音，再决定要不要往上游提 PR。
