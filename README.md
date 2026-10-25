# bilibili-ma-provider

把 Bilibili 视频音轨接入 [Music Assistant](https://music-assistant.io) 的非官方音乐 provider。

> 状态：**alpha，可本地跑通**。目标 MA **2.10.8**（兼容 2.10.7），`music-assistant-models==1.1.205`，Python 3.14。
> 只做流式播放：不下载、不落盘、不绕过会员/付费内容。请遵守 B 站服务条款，仅个人使用。

## 已验证

- MA 2.10.8 容器内 provider 正常加载、可搜索、可出流。
- `get_stream_details()` → `ffmpeg -headers` 真实拉取 2 秒音频成功（AAC 48 kHz stereo，支持 seek）。
- 匿名可用；登录后可同步收藏夹（只读）。
- 单元测试：`176 passed, 1 skipped`；`ruff check` 通过。

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
docker build -f docker/Dockerfile -t bilibili-ma:2.10.8 .
docker compose -f docker/compose.build.yml up -d
```

镜像里预装 `segno==1.6.6`（二维码登录用）；stock 镜像没有它，setup flow 会自动降级为手填 SESSDATA。

## 分发状态

- 当前：本地 Docker / bind mount / venv 脚本。
- 之后：稳定 1 周后再打 PyPI 包 + `music-assistant-plugin-manager`（见 `docs/implementation-plan.md` M8）。
- HAOS add-on：MA 只从包内 `providers/` 发现 provider，**没有官方注入点**；要全覆盖只能上游合并。

## 免责声明

本项目与 Bilibili 无隶属或授权关系，不内置任何音视频资源。仅用于学习、研究与个人使用；请遵守当地法律与平台条款，不要用于侵权或规避平台限制。
