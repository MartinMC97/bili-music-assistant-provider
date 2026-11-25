# 用于 Music Assistant 的 Bilibili Provider

[![CI](https://github.com/MartinMC97/bili-music-assistant-provider/actions/workflows/ci.yml/badge.svg)](https://github.com/MartinMC97/bili-music-assistant-provider/actions/workflows/ci.yml)
[![上游稳定版](https://github.com/MartinMC97/bili-music-assistant-provider/actions/workflows/upstream-stable.yml/badge.svg)](https://github.com/MartinMC97/bili-music-assistant-provider/actions/workflows/upstream-stable.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

[English](README.md) | **简体中文**

把 B 站视频的音轨接入 [Music Assistant](https://music-assistant.io) 的非官方音乐 provider。

本项目只做只读、流式播放：不下载媒体、不修改你的 B 站账号，也不绕过会员/付费内容。请仅用于个人用途，并遵守 B 站服务条款。

> 状态：alpha。provider 面向 Music Assistant 2.10 稳定版；与 Bilibili、Music Assistant 官方均无隶属或背书关系。

## 功能

- **搜索** B 站视频并映射为可播放曲目：清洗标题噪声词、按时长过滤（默认 60–900 秒）、去重并按播放量排序。
- **直达** 完整的 `BV...` 或 `av<数字>`：直接返回该视频的单条曲目；如果视频属于某个 UP 合集，搜索结果里会同时给出合集。
- **DASH 播放**：AAC 64/132/192 kbps、Hi-Res FLAC 或 Dolby，取决于账号权益和 provider 里设置的音质上限。交给 ffmpeg 的流会带上 CDN 要求的 `Referer`、`Origin` 和桌面版 `User-Agent`。
- **登录方式**：扫码（推荐）、手填 `SESSDATA` cookie（读收藏夹还需要 `DedeUserID`），或匿名使用。凭据过期会自动降级为匿名，不会让 provider 失效。
- **收藏夹**（需要登录）作为只读播放列表同步（已处理分页），其中的 B 站音频（`au`）条目也会映射为可播放曲目；番剧/OGV 等不可播放条目会跳过。
- **首页推荐与浏览**：音乐榜单、新歌、分区音乐榜、收藏夹。
- **UP 合集**（合集 / UGC season）映射为播放列表，见下方 [UP 合集作为播放列表](#up-合集作为播放列表)。
- **B 站音频**（`au` 曲目）走音频专用接口；该接口通常只有单一码率，所以 DASH 音质选项对音频曲目不生效。

## 环境要求

| 组件 | 要求 |
|---|---|
| Music Assistant | 2.10.7 及以上；当前按 stable 2.10.13 构建与测试 |
| `music-assistant-models` | 1.1.205 及以上（随 Music Assistant 一起安装） |
| 开发环境 | Python 3.14 和 Docker（dev 容器内自带 Music Assistant 与 ffmpeg） |

Music Assistant 目前处于 2.10 稳定版线；2.11 上游仍是 beta，尚未用本 provider 验证。

本 provider 不是 PyPI 包，MA 只会扫描 `music_assistant/providers/<domain>/` 下的目录，所以「安装」本质上就是把 `provider/bilibili` 放进 MA 包目录（或使用下面的镜像）。

## 安装

完整的分步说明见 **[docs/INSTALL.md](docs/INSTALL.md)**（中文）。简要版如下。

### 方式 A：使用预构建镜像（推荐）

GitHub Actions 已经把 provider 打进 MA 镜像，多架构（`linux/amd64` / `linux/arm64`），包是公开的，拉取不需要登录：

```bash
docker pull ghcr.io/martinmc97/music-assistant:latest
```

把现有 MA 的 `image:` 换成这个地址，其它配置保持不动：

```yaml
services:
  music-assistant:
    image: ghcr.io/martinmc97/music-assistant:latest
    container_name: music-assistant
    restart: unless-stopped
    network_mode: host          # MA 靠 host 网络发现播放器
    volumes:
      - /path/to/ma-data:/data  # 沿用你原来的数据目录
    environment:
      - LOG_LEVEL=info
```

```bash
docker compose up -d
```

不用 compose 的话：

```bash
docker run -d --name music-assistant \
  --network host \
  -v /path/to/ma-data:/data \
  -e LOG_LEVEL=info \
  ghcr.io/martinmc97/music-assistant:latest
```

- 镜像里已经包含 `provider/bilibili` 和 `segno`（扫码登录用），启动后不需要再装任何东西。
- `latest` 跟随上游最新的 MA stable。想固定版本就用 `2.10.13-bili-<7 位 commit>` 形式的标签，例如 `ghcr.io/martinmc97/music-assistant:2.10.13-bili-11079f7`。
- 需要指定架构时用 `docker pull --platform linux/arm64 ...`。
- 群晖/威联通等用 Container Manager 的，把镜像地址填进「映像」并同样映射 `/data` 即可。

### 方式 B：自己构建镜像（改代码时用）

```bash
git clone https://github.com/MartinMC97/bili-music-assistant-provider.git bilibili-ma-provider
cd bilibili-ma-provider

# MA_VERSION 决定上游基础镜像，改成你自己实例的版本
docker build -f docker/Dockerfile --build-arg MA_VERSION=2.10.13 \
  -t music-assistant:2.10.13-bili .
```

然后把 compose 里的 `image:` 换成 `music-assistant:2.10.13-bili`，其它配置保持不变。Dockerfile 会把 provider 复制进 MA 的 provider 目录、安装 `segno==1.6.6`，并保留官方 ENTRYPOINT。

只想临时起一个带 provider 的实例时，也可以一步完成：

```bash
docker compose -f docker/compose.build.yml up -d
```

### 方式 C：塞进运行中的容器（快速试玩）

```bash
./scripts/install_docker.sh music-assistant   # 容器名，默认 music-assistant
```

脚本会把 provider 复制进容器并重启。容器重建或镜像更新后这份拷贝会丢失，长期使用请选方式 A 或 B。

### 方式 D：裸机 / venv

```bash
/path/to/ma-venv/bin/python scripts/install_venv.py
```

用 MA 所在 Python 环境解释器运行。脚本只把 `provider/bilibili` 复制到该解释器的 `music_assistant/providers` 目录，不下载任何东西。MA 升级/重装会覆盖包目录，升级后需要重新运行一次。

### 安装完成之后

重启 Music Assistant，打开 **Settings → Providers → Add Provider → Bilibili**，选择登录方式。推荐扫码登录，这样也能读取收藏夹；匿名模式可以搜索和播放。

Home Assistant OS 没有给 MA Add-on 注入 provider 的入口，因此不支持 Add-on 安装，详见 [docs/INSTALL.md](docs/INSTALL.md)。

### 更新

```bash
docker compose pull music-assistant   # 或 docker pull <image>
docker compose up -d
```

provider 更新不影响 `/data`。MA 启动时会自动迁移自己的数据库；升级 MA 本身之前建议先备份数据目录。

## 配置项

所有选项都在 provider 设置里，账号凭据由 setup flow 收集。

| 选项 | 默认值 | 说明 |
|---|---|---|
| 音质上限 | `auto` | 使用账号允许的最好音质。FLAC 和 Dolby 需要大会员；也可以固定 AAC 192/132/64 kbps。 |
| 最短时长 | 60 秒 | 关键词搜索结果中短于该值的曲目会被丢弃，`0` 表示不设下限。 |
| 最长时长 | 900 秒 | 关键词搜索结果中长于该值的曲目会被丢弃（长混音、课程等），`0` 表示不设上限。 |
| 搜索结果上限 | 20 | 每次搜索最多返回的曲目数（1–50）。 |
| 播放前探测 CDN | 开 | 向首选 CDN 域名发一个 1 KB 的 Range 请求，失败时改用备用地址。 |
| 手填 SESSDATA | 空 | 扫码不可用时的兜底登录；读收藏夹还需要 `DedeUserID`。 |

登录方式：

| 方式 | 能做什么 |
|---|---|
| 扫码 | 完整登录：可用账号权益内的更高音质、读取收藏夹。依赖 `segno`，镜像里已内置。 |
| 手填 `SESSDATA` + `DedeUserID` | 与扫码相同的权限，不依赖可选包。 |
| 匿名 | 可搜索、看榜单和播放，但只有匿名音质，且没有收藏夹。 |

## UP 合集作为播放列表

B 站的「合集」（`ugc_season`）是 UP 主整理的一组视频，这里映射成 MA 的**播放列表**。合集必须显式指定：普通关键词搜索不会混入合集。

| 入口 | 说明 |
|---|---|
| `BSEASON:7817533` | 用合集 id 直接打开该合集。 |
| `https://space.bilibili.com/3546851714271831/lists/7817533?type=season` | 直接粘合集链接（`channel/collectiondetail?sid=` 也支持）。 |
| `UP:3546851714271831`（或该 UP 的空间链接） | 列出这个 UP 的全部合集。 |
| 搜 `BV1FMsn6REhn` | 如果该视频属于某个合集，结果里会同时出现视频和它所在的合集。 |

合集出现在搜索结果的「播放列表」分区，曲目按页加载（每页 100 条），一个 169 集的合集两页即可加载完。已知限制：

- 只做合集，不做「系列」（`type=series`）和 UP 全量投稿；
- 合集内容按原样播放，不套用搜索页的 60–900 秒时长过滤，长混音也能播；
- 合集不会自动进 MA 曲库（provider playlist 机制），通过搜索/ID 打开即可播放；
- 合集 id 是数字，**纯数字单独搜索不会被当成合集**，避免和歌名混淆。

## 预构建镜像与标签

镜像发布在：

```text
ghcr.io/martinmc97/music-assistant
```

- 平台：`linux/amd64`、`linux/arm64`；包是 public 的。
- 标签：`2.10.13-bili-<7 位 commit>`（MA 版本 + 某次 provider 构建）、`latest`、`main`、`sha-<sha>`；打 `v*` tag 还会生成 semver 标签（`0.1.0`、`0.1`）。
- [`docker-publish.yml`](.github/workflows/docker-publish.yml)：推 `main` 或 `v*` tag 时构建。默认自动查询上游最新的 MA stable；手动触发可以指定版本；仓库变量 `MA_VERSION` 只在 GitHub API 查询失败时兜底。
- [`upstream-stable.yml`](.github/workflows/upstream-stable.yml)：每 6 小时检查一次上游 stable。发现新版本且 GHCR 上还没有对应的 `<version>-bili-<commit>` 时，先用该版本跑测试，通过后构建、推送，并把 `latest` 指过去。

固定当前稳定版构建：

```bash
docker pull ghcr.io/martinmc97/music-assistant:2.10.13-bili-11079f7
```

## 已知限制

- 不支持歌词。
- 一个视频只映射为一条曲目：多 P 视频暂未展开为分 P 曲目。
- 不支持番剧/OGV，收藏夹里的这类条目会被跳过。
- 只开放 UP 合集：「系列」列表不支持；UP 全量投稿列表被 B 站风控拦得很严（实测很少能成功）。
- 关键词搜索只返回曲目；播放列表只来自显式合集查询和你的收藏夹。
- B 站音频（`au`）通常只有单一码率，FLAC/Dolby 选项只对视频 DASH 流生效。
- 内容可用性和音质取决于地区、账号权益以及 B 站的风控；播放地址几小时后过期，每次播放都会重新解析。
- provider 不会写入你的 B 站账号，收藏夹保持只读。

## 目录

```text
provider/bilibili/   # provider 源码（MA domain = bilibili）
tests/               # 单元测试 + 录制 fixture
docker/              # 独立 dev 服务器 + 可构建镜像
scripts/             # 开发、安装与真实 E2E 脚本
docs/                # 契约、安装指南、计划与开发日志
artifacts/           # 分工笔记与代码评审
```

## 本地开发

dev 容器跑一个独立的 MA 实例，并把源码目录挂进去，不会碰你现有的安装：

```bash
# 起独立 MA（http://localhost:18095，数据在 .dev-data/）
docker compose -f docker/compose.dev.yml up -d

# 改完代码后重启并看日志
./scripts/dev.sh
```

首次启动后访问 <http://localhost:18095> 创建管理员账号，然后在 Settings → Providers 里添加 **Bilibili**。

dev compose 会把 provider 挂进容器里的 MA 包目录，挂载路径跟着镜像的 Python 版本走（`.../lib/python3.14/site-packages/music_assistant/`）；换基础镜像时要同步修改。

在容器里跑与上游一致的检查（工具版本在 `pyproject.toml` 里锁定）：

```bash
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/ruff check provider tests
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/ruff format --check provider tests
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python -m mypy
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/codespell provider tests scripts docs README.md README.zh-CN.md
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python scripts/check_provider_conventions.py
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python -m pytest -q
```

`.pre-commit-config.yaml` 提供等价的本地钩子（`pre-commit install`）。

### 真实播放端到端检查

以下脚本会访问真实 B 站 API/CDN 并调用 ffmpeg，不属于单元测试：

```bash
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python scripts/e2e_stream.py
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python scripts/e2e_collections.py
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python scripts/e2e_audio.py
```

单元测试不联网；联网集成测试需要显式开启：`RUN_LIVE=1 python -m pytest -m live tests/integration/`。

## 参考与致谢

- [**HanversionOvO/BiliMusic**](https://github.com/HanversionOvO/BiliMusic)：B 站接口行为、WBI 签名、扫码登录流程、DASH 音轨选择与标题清洗思路的参考。
- [**music-assistant/server**](https://github.com/music-assistant/server)：provider API、`StreamDetails` 与流式播放模型。
- **Bilibili 公开 Web API**：视频/音频接口路径与播放协议。

> BiliMusic 仓库当前没有 `LICENSE` 文件。这里仅作接口行为与算法思路参考，未包含其源代码；如果上游后续补充许可证，或你认为某段实现与它过于接近，请开 issue，我们会调整或重写。

## License

[Apache-2.0](LICENSE)。版权与归属见 [NOTICE](NOTICE)。

本项目为**非官方项目**，与 Bilibili 无隶属、授权或背书关系；Apache-2.0 只授予代码版权许可，不授予 Bilibili 名称、logo 或商标的使用权。

## 免责声明

本项目不内置任何音视频资源。仅用于学习、研究与个人使用；请遵守当地法律与平台条款，不要用于侵权或规避平台限制。
