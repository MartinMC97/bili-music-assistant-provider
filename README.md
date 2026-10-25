# bilibili-ma-provider

把 Bilibili 视频音轨接入 [Music Assistant](https://music-assistant.io) 的非官方音乐 provider。

> 状态：**开发中（alpha）**。当前目标 MA **2.10.8**（兼容 2.10.7），`music-assistant-models==1.1.205`。
> 只做流式播放：不下载、不落盘、不绕过会员/付费内容。请遵守 B 站服务条款，仅个人使用。

## 能力

- 搜索 B 站视频并映射为曲目
- 播放 DASH 音轨（AAC / FLAC / Dolby，取决于账号权益）
- 扫码登录 / 手填 SESSDATA / 匿名使用
- 收藏夹同步（只读，需要登录）
- 榜单/新歌推荐与浏览
- 不做歌词、不做 UP 主作品列表（接口被风控）

## 目录

```text
provider/bilibili/     # 唯一源码树（domain = bilibili）
tests/                 # 单元测试 + fixture
docker/                # 本地开发 compose
scripts/               # dev / 安装脚本
docs/                  # 实现计划、契约、开发日志
```

## 本地开发

```bash
# 1. 起一个独立的 MA（端口 18095，不碰现有实例）
docker compose -f docker/compose.dev.yml up -d

# 2. 改代码后重启并看日志
./scripts/dev.sh
```

首次启动后访问 `http://localhost:18095` 创建管理员账号，然后在 Settings → Providers 里添加 **Bilibili**。

开发计划与契约见 `docs/implementation-plan.md`、`docs/contracts.md`；过程记录见 `docs/devlog.md`。

## 免责声明

本项目与 Bilibili 无隶属或授权关系，不内置任何音视频资源。仅用于学习、研究与个人使用；请遵守当地法律与平台条款，不要用于侵权或规避平台限制。
