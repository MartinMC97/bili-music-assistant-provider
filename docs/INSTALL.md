# 安装 Bilibili Provider 到自己的 Music Assistant

本文面向「拿到这个 repo、想装到自己 MA 实例」的用户。

> 适用版本：**Music Assistant 2.10.7 / 2.10.8**（`music-assistant-models==1.1.205`，Python 3.14）。
> 2.11 目前只有 beta/dev，未做验证；更老的版本没有 `StreamDetails.extra_input_args`，会加载失败。
> 只支持流式播放：不下载、不落盘、不绕过会员/付费内容。请遵守 B 站服务条款，仅个人使用。

---

## 0. 先确认你的 MA 是怎么装的

| 安装方式 | 怎么判断 | 本文是否支持 |
|---|---|---|
| Docker / docker compose | `docker ps` 里能看到 `ghcr.io/music-assistant/server` | ✅ 方式 A / B |
| 裸机 / venv / 源码 | MA 跑在某个 Python venv 或系统 Python 里 | ✅ 方式 C |
| Home Assistant OS Add-on | HA → 设置 → 加载项 → Music Assistant | ❌ 无官方注入点，见方式 D |
| HA Container + 独立 MA 容器 | MA 是单独 Docker 容器 | ✅ 按 Docker 处理 |

MA 发现 provider 的规则是：**只扫描 `music_assistant/providers/<domain>/manifest.json`**。
所以「装 provider」本质上是把 `provider/bilibili/` 这个目录放进 MA 包目录，然后重启 MA。

> 不要用 `pip install` 装这个 repo——它还不是 PyPI 包，MA 也不会从 site-packages 顶层发现 provider。

---

## 方式 A：构建自带 provider 的 Docker 镜像（推荐，持久）

适合长期使用，MA 重建/升级后仍然保留。

```bash
git clone https://github.com/MartinMC97/bili-music-assistant-provider.git bilibili-ma-provider
cd bilibili-ma-provider

# 默认基于 2.10.8；如果自己的 MA 是 2.10.7，把 build-arg 改成 2.10.7
docker build -f docker/Dockerfile \
  --build-arg MA_VERSION=2.10.8 \
  -t bilibili-ma:2.10.8 .
```

然后把你现有 compose 里的 `image:` 换掉（**其它配置保持不动**）：

```yaml
services:
  music-assistant:
    image: bilibili-ma:2.10.8
    # 原来的 ports / volumes / environment / network_mode 都保留
```

```bash
docker compose up -d
```

如果之前是 `docker run`：

```bash
docker run -d --name music-assistant \
  -p 8095:8095 \
  -v /path/to/ma-data:/data \
  bilibili-ma:2.10.8
```

这个镜像会：

- 把 `provider/bilibili` 复制进 MA 包目录；
- 预装 `segno==1.6.6`（二维码登录用）；
- 保留官方镜像的 ENTRYPOINT，`--data-dir /data` 等参数不变。

---

## 方式 B：bind mount 进现有容器（最快，适合先试）

### 1. 找到容器里的 provider 目录

```bash
docker exec music-assistant /app/venv/bin/python -c \
  'import music_assistant, os; print(os.path.join(os.path.dirname(music_assistant.__file__), "providers"))'
```

输出类似：

```text
/app/venv/lib/python3.14/site-packages/music_assistant/providers
```

> 注意：官方镜像的包在 **venv 的 site-packages** 里，不是 `/app/music_assistant`。
> 路径里的 `python3.14` 会随镜像 Python 版本变化，以上面命令的输出为准。

### 2. 在 compose 里加挂载

```yaml
services:
  music-assistant:
    image: ghcr.io/music-assistant/server:2.10.8
    environment:
      PYTHONDONTWRITEBYTECODE: "1"   # 只读挂载时避免写 __pycache__ 报错
    volumes:
      - /绝对路径/bilibili-ma-provider/provider/bilibili:/app/venv/lib/python3.14/site-packages/music_assistant/providers/bilibili:ro
      - /path/to/ma-data:/data       # 你原来的 data 挂载
```

```bash
docker compose up -d
```

### 3. 安装二维码依赖（可选）

```bash
docker exec music-assistant /app/venv/bin/uv pip install --no-cache segno==1.6.6
```

不装也能用：setup flow 会自动降级为「手填 SESSDATA」或「匿名使用」。

> bind mount 的 provider 代码在宿主机上，升级只需 `git pull` + 重启容器。
> 但容器本身如果被 `docker compose down` / 换镜像重建，**挂载要保留在 compose 里**才不丢。

### 快捷脚本（会写进容器层，重建即丢）

```bash
./scripts/install_docker.sh music-assistant
# 或：./scripts/install_docker.sh music-assistant --no-segno --force
```

这个脚本适合临时验证；长期使用请用方式 A 或 B。

---

## 方式 C：裸机 / venv / 源码运行

用 **MA 所在 venv 的 Python** 执行安装脚本：

```bash
git clone https://github.com/MartinMC97/bili-music-assistant-provider.git bilibili-ma-provider
cd bilibili-ma-provider

/path/to/ma-venv/bin/python scripts/install_venv.py
# 已存在旧版本时：
/path/to/ma-venv/bin/python scripts/install_venv.py --force
```

脚本会自动定位：

```text
<site-packages>/music_assistant/providers/bilibili
```

然后安装可选依赖并重启 MA：

```bash
/path/to/ma-venv/bin/pip install segno==1.6.6
# systemd 单元名各发行版不同，例如：
sudo systemctl restart music-assistant
```

手动安装等价于：

```bash
PKG=$(/path/to/ma-venv/bin/python -c \
  'import music_assistant, os; print(os.path.dirname(music_assistant.__file__))')
cp -r provider/bilibili "$PKG/providers/bilibili"
```

> MA 升级/重装会覆盖包目录，升级后需要重跑脚本。
> 卸载：`/path/to/ma-venv/bin/python scripts/uninstall_venv.py --yes`，再到 UI 删除实例。

---

## 方式 D：Home Assistant OS Add-on

**目前不支持直接把文件塞进 add-on。**

HAOS 的 Music Assistant add-on 没有官方 provider 注入点：

- 不能 `docker cp`（没有宿主机 Docker 访问权限）；
- add-on 更新会重建容器，手工改动会丢；
- MA 只从包内 `providers/` 发现 provider，没有“上传 provider”功能。

可行路径只有：

1. **改用独立 Docker 版 MA**（推荐）：在另一台机器/NAS 上用方式 A 跑 `bilibili-ma` 镜像，HA 里通过 Music Assistant 集成指向这个实例。功能等价，且能自由装 provider。
2. **自建 add-on**：fork 官方 add-on 仓库，在构建阶段把 provider 复制进去。维护成本高，本文不提供现成脚本。
3. **等上游合并**：把 provider 提进 `music-assistant/server`，所有安装方式自然可用。

---

## 安装后：在 MA 里启用

1. 重启 MA，确认日志没有 traceback（可选）：
   ```bash
   docker logs --tail=100 music-assistant | grep -i bilibili
   ```
   正常会看到 `Loaded music provider Bilibili`（或至少没有 `Unknown provider domain`）。
2. 打开 MA → **Settings → Providers → Add Provider → Bilibili**。
3. 选择登录方式：

   | 选项 | 说明 |
   |---|---|
   | **QR code** | 用 Bilibili 手机 App 扫码；需要 `segno`，180 秒有效 |
   | **Paste SESSDATA cookie** | 手填 `SESSDATA`（可选 `DedeUserID`）；不需要 segno |
   | **Continue anonymously** | 不登录；能搜索/播放，但没有收藏夹 |

4. 登录成功后，收藏夹会在后台同步到 MA 的 Playlists / Library。
5. 搜索一首歌测试播放；播放时走 `StreamDetails` + ffmpeg，不落盘。

### 配置项

| 配置 | 默认 | 说明 |
|---|---|---|
| 音质上限 | auto | `auto` / `flac` / `dolby` / `aac192` / `aac132` / `aac64` |
| 最短/最长时长 | 60 / 900 秒 | 过滤短视频、长循环、直播回放 |
| 搜索结果数量 | 20 | 单次搜索上限 |
| 播放前探测 CDN | 开 | 1KB Range 探测，失败切备份地址 |

---

## 验证安装成功

```bash
# 1. provider 文件真的在容器里
docker exec music-assistant ls \
  /app/venv/lib/python3.14/site-packages/music_assistant/providers/bilibili/manifest.json

# 2. 日志没有加载错误
docker logs --tail=200 music-assistant 2>&1 | grep -iE "bilibili|error|traceback"
```

然后在 MA 里搜索「晴天」，能出结果并播放就通了。

---

## 升级 / 卸载

| 方式 | 升级 | 卸载 |
|---|---|---|
| A. 自定义镜像 | 改 `MA_VERSION` 重新 build，`compose up -d` | 换回官方镜像 + 删实例 |
| B. bind mount | `git pull` + 重启容器 | 删 compose 挂载 + 重启 |
| C. venv | 重跑 `install_venv.py --force` | `uninstall_venv.py --yes` |
| D. HAOS | 不支持 | — |

卸 provider 后，记得在 MA UI 里删除 Bilibili 实例，并清理它的 library 数据。

---

## 常见问题

| 现象 | 原因 / 解决 |
|---|---|
| `Unknown provider domain: bilibili` | 目录层级不对（必须是 `providers/bilibili/manifest.json`），或没重启 MA |
| Add Provider 里没有 Bilibili | 看启动日志；确认挂载路径的 Python 版本对；确认 `manifest.json` 可读 |
| 只有手动/匿名，没有二维码 | 容器里没装 `segno`；按方式 B 第 3 步安装，或直接用 SESSDATA |
| 搜索报 `-352` / `-412` | B 站风控；等几分钟、降低频率，或登录后再试 |
| 播放 403 / 无声音 | 容器网络到 B 站 CDN 不通；确认没有代理改坏 `Referer`/`Range`；换网络试 |
| 收藏夹是空的 | 当前是匿名或登录已失效；Providers → Bilibili → Reconfigure 重新扫码 |
| 收藏夹里有做饭/游戏视频 | 这是你 B 站收藏夹本身的内容；provider 不做内容类型过滤 |
| `Permission denied: __pycache__` | 只读挂载导致；设 `PYTHONDONTWRITEBYTECODE=1` 或改 `:rw` |
| MA 升级后 provider 消失 | 包目录被覆盖；按对应方式重装 |
| HAOS add-on 装不上 | 见方式 D，目前无官方注入点 |
| `missing StreamDetails.extra_input_args` | MA 版本太旧；需要 2.10.7+ |

---

## 合规提醒

- 本项目与 Bilibili 无隶属或授权关系，不内置任何音视频资源。
- 仅用于学习、研究与个人使用；不要用于下载、二次分发、绕过会员/付费或规避平台限制。
- 不要把暴露在公网的 MA 实例设成弱口令；本地临时口令在公网部署前必须改掉。
