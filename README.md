# bilibili-ma-provider

把 Bilibili 视频音轨接入 [Music Assistant](https://music-assistant.io) 的非官方音乐 provider。

> 状态：**alpha，可本地跑通**。目标 MA **2.10.8**（兼容 2.10.7），`music-assistant-models==1.1.205`，Python 3.14。
> 只做流式播放：不下载、不落盘、不绕过会员/付费内容。请遵守 B 站服务条款，仅个人使用。

## 已验证

- MA 2.10.8 容器内 provider 正常加载、可搜索、可出流。
- `get_stream_details()` → `ffmpeg -headers` 真实拉取 2 秒音频成功（AAC 48 kHz stereo，支持 seek）。
- 匿名可用；登录后可同步收藏夹（只读）。
- 单元测试：`176 passed, 1 skipped`；`ruff check` 通过。

## 安装到自己的 Music Assistant

完整步骤见 **[docs/INSTALL.md](docs/INSTALL.md)**。三种常用方式：

```bash
# A. 构建自带 provider 的镜像（推荐，持久）
docker build -f docker/Dockerfile -t music-assistant:2.10.8-bili .
# 然后把 compose 里的 image 换成 music-assistant:2.10.8-bili

# B. 快速塞进运行中的容器（重建会丢，适合先试）
./scripts/install_docker.sh music-assistant

# C. 裸机 / venv（用 MA 所在 venv 的 python）
/path/to/ma-venv/bin/python scripts/install_venv.py
```

装完重启 MA，在 **Settings → Providers → Add Provider → Bilibili** 里扫码或填 SESSDATA 即可。
HAOS Add-on 没有官方 provider 注入点，见 INSTALL.md 的方式 D。

## 能力

- 搜索 B 站视频并映射为曲目（标题清洗、时长过滤、去重）
- 播放 DASH 音轨（AAC / FLAC / Dolby，取决于账号权益）
- 扫码登录 / 手填 SESSDATA / 匿名使用
- 收藏夹同步（只读，需要登录；整夹分页已处理）
- 榜单/新歌推荐与浏览
- 不做歌词、不做 UP 主作品列表（接口被风控）

## 目录

```text
provider/bilibili/     # 唯一源码树（domain = bilibili）
tests/                 # 单元测试 + 录制 fixture
docker/                # 本地开发 compose + 可构建镜像
scripts/               # dev / live E2E / 安装脚本
docs/                  # 实现计划、契约、开发日志
artifacts/             # 智能体分工笔记与代码评审
```

## 本地开发（不碰现有 MA）

```bash
# 1. 起一个独立的 MA（端口 18095）
docker compose -f docker/compose.dev.yml up -d

# 访问 http://localhost:18095 或 http://localhost:8095

# 2. 改代码后重启并看日志
./scripts/dev.sh

# 3. 跑单测 / lint（容器内有 Python 3.14 + MA 模型）
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python -m pytest -q
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/ruff check provider tests

# 4. 真实播放 E2E（会访问 B 站 API/CDN）
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python scripts/e2e_stream.py
```

首次启动后访问 `http://localhost:18095` 创建管理员账号，然后在 Settings → Providers 里添加 **Bilibili**。

> 官方镜像把 MA 包放在 venv 的 site-packages 里（2.10.8 是
> `/app/venv/lib/python3.14/site-packages/music_assistant`），compose 里的挂载路径按此设置。
> Python 版本变化时路径会变，Dockerfile 用 `python -c` 动态定位。

## 构建自带 provider 的镜像

```bash
docker build -f docker/Dockerfile -t music-assistant:2.10.8-bili .
docker compose -f docker/compose.build.yml up -d
```

镜像里预装 `segno==1.6.6`（二维码登录用）；stock 镜像没有它，setup flow 会自动降级为手填 SESSDATA。

## 预构建镜像（GHCR）

推 `main` 或 `v*` tag 时，GitHub Actions 会自动构建并推送：

```text
ghcr.io/martinmc97/music-assistant
```

- 平台：`linux/amd64`、`linux/arm64`
- 标签：`2.10.8-bili-<commit>`（MA 2.10.8 + 某次 provider 构建，commit 为 7 位短 id）、`latest`（main）、`main`、`sha-<commit>`；tag `v0.1.0` 额外生成 `0.1.0`、`0.1`
- 基础 MA 版本由仓库变量 `MA_VERSION` 控制（默认 `2.10.8`）；手动触发时留空即使用该变量。改版本后会自动生成对应的 `<version>-bili-<commit>` 标签
- 旧镜像名 `ghcr.io/martinmc97/bili-music-assistant-provider` 不再更新

包默认继承仓库可见性（private）。拉取前登录：

```bash
echo "$CR_PAT" | docker login ghcr.io -u MartinMC97 --password-stdin
docker pull ghcr.io/martinmc97/music-assistant:latest

# 固定到某次 provider 构建
docker pull ghcr.io/martinmc97/music-assistant:2.10.8-bili-<commit>
```

如果想让别人免登录拉取，在 GitHub → **Packages** → 该镜像 → Package settings 里把可见性改成 public。

## 分发状态

- 当前：本地 Docker / bind mount / venv 脚本。
- 之后：稳定 1 周后再打 PyPI 包 + `music-assistant-plugin-manager`（见 `docs/implementation-plan.md` M8）。
- HAOS add-on：MA 只从包内 `providers/` 发现 provider，**没有官方注入点**；要全覆盖只能上游合并。

## 参考与致谢

- [**HanversionOvO/BiliMusic**](https://github.com/HanversionOvO/BiliMusic)：B 站接口行为、WBI 签名/扫码登录流程、DASH 音轨选择、标题清洗与噪声词思路的参考。BiliMusic 是 TypeScript/Electron 客户端；本仓库的 provider 是用 Python 按 Music Assistant 接口重写的，**没有复制它的 Electron/React 代码**。
- [**music-assistant/server**](https://github.com/music-assistant/server)：provider API、`StreamDetails` 与流式播放模型。
- **Bilibili 公开 Web API**：视频/音频接口路径与播放协议。

> BiliMusic 仓库当前没有 `LICENSE` 文件。这里仅作接口行为与算法思路参考，未包含其源代码；如果上游后续补充许可证，或你认为某段实现与它过于接近，请开 issue，我们会调整或重写。

## License

[Apache-2.0](LICENSE)。版权与归属见 [NOTICE](NOTICE)。

本项目为**非官方项目**，与 Bilibili 无隶属、授权或背书关系；Apache-2.0 只授予代码版权许可，不授予 Bilibili 名称、logo 或商标的使用权。

## 免责声明

本项目不内置任何音视频资源。仅用于学习、研究与个人使用；请遵守当地法律与平台条款，不要用于侵权或规避平台限制。
