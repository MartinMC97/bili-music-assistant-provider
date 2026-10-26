# 开发日志

> 记录人：lead + agent team。格式：`时间 | 阶段 | 动作 | 结果/备注`。
> 所有时间以 Asia/Shanghai 为准。

## 2026-10-25

| 时间 | 阶段 | 动作 | 结果/备注 |
|---|---|---|---|
| 23:04 | 启动 | 接收开发任务，确认目标 MA 2.10.8、独立仓库、本地 Docker、收藏夹必需 | 不动现有生产 MA |
| 23:06 | 调研 | 核对 MA 2.10.8 源码：`music-assistant-models==1.1.205`，`MusicProvider` 单类，`extra_input_args` 存在 | 2.10.7 与 2.10.8 provider API 一致 |
| 23:08 | 环境 | 拉取 `ghcr.io/music-assistant/server:2.10.8`，容器 Python 3.14.7 | 镜像 digest `661e88be…` |
| 23:10 | 仓库 | 创建 `bilibili-ma-provider/` + `git init`，初始骨架 | git 身份：BiliMusic MA Dev |
| 23:12 | 契约 | 写 `docs/contracts.md`：模块边界、client/parsers/catalog 接口、测试要求 | 供并行 agent 使用 |
| 23:20 | 团队 | 并行派发 4 个模块 agent：client/wbi、parsers、auth/setup_flow、catalog | 全部返回 ok；各自写 `artifacts/notes-*.md` |
| 23:32 | 团队 | provider 集成 agent 完成 `__init__.py`（937 行）与 `test_provider.py` | 方法齐全：search/get_track/get_stream_details/收藏夹/推荐/browse |
| 23:36 | 团队 | tests agent 补 stream selection、error mapping、live smoke | 174 passed / 1 skipped（live 默认 skip） |
| 23:46 | 评审 | reviewer 只读审查，输出 `artifacts/review.md` | 1×P0（setup 非 async）+ 5×P1 + 若干 P2 |
| 23:50 | 修复 | P0：`setup()` 改回 `async def`；测试改 `await setup()` | MA 2.10.8 实测重新加载成功 |
| 23:51 | 修复 | P1：`-352/-412` 不再误判登录失效；匿名重配写空 `credential_json`；quality 严格上限；setup flow 捕获 `ResourceTemporarilyUnavailable`；`ruff` RUF001 关掉 | 单测 176 passed |
| 23:52 | E2E | `scripts/e2e_stream.py`：真实 playurl → StreamDetails → `ffmpeg -headers` 解码 2s | 成功；AAC 48k/2ch，expiration 7140s |
| 23:53 | E2E | MA API `music/search` 搜「晴天」 | 返回 3 条；标题清洗、provider mapping 正常 |
| 23:54 | E2E | MA setup flow：`config/providers/setup` → form(choose) → submit anonymous → finish | 第二个实例成功创建并加载 |
| 23:55 | 分发 | `docker/Dockerfile` 构建 `bilibili-ma:2.10.8`，动态定位 site-packages + 预装 segno | 构建成功；容器内 `provider=True`、`segno=1.6.6` |
| 23:56 | 修复 | P2：收藏夹改为“一次拉全 + 按页切片”，position 全局连续、空页不终止；`av<aid>` 无 cid 可解析；cookie 合并；view 缓存淘汰；AudioFormat 显式 codec_type | 单测 176 passed；待真实登录账号验证收藏夹 |
| 23:57 | 文档 | README 补齐构建/测试/E2E；implementation-plan 修正官方镜像实际路径 | 代码与文档一致 |

## 当前状态（2026-10-25 23:57）

- ✅ v0.1 目标（匿名搜索 + 播放 + seek）已在 MA 2.10.8 容器内跑通。
- ✅ v0.2 的扫码/手填/匿名 setup flow 已接入 MA，匿名路径 E2E 通过。
- ✅ 收藏夹代码与单测完成；**尚未用真实登录账号跑过**，需要一条真实 SESSDATA 做验收。
- ✅ Dockerfile 可构建；stock 镜像扫码依赖已预装。
- ⏳ 未做：真实登录后的 FLAC/Dolby 验证、MA UI 里的收藏夹整夹播放、`user_name` owner 显示、PyPI 打包。

## 待办 / 风险

1. 用真实账号验证：收藏夹列表、跨页 position、失效稿件跳过、整夹播放。
2. 确认 `-352` 在登录后是否仍影响搜索；目前搜索匿名可用。
3. `get_favorite_playlist` 仍是 O(n) 扫描，收藏夹多时可加 300s 缓存。
4. 真实 `dash.flac.audio` / `dolby.audio` 形态需大会员账号验证（单测已覆盖 dict/list 两种）。
5. 上游 PR 的合规表述与 `stage: alpha` 需要再确认。
| 23:58 | 交付 | commit `ecf8844`：44 files, +7843/-45；最终回归 `176 passed, 1 skipped`、ruff/format 通过 | 工作树干净；本地 dev 容器与 `bilibili-ma:2.10.8` 镜像可用 |
| 07:25 | 排障 | 用户反馈访问不了；容器 Up 7h，本机/各网卡 200，日志无外部连接 | 判定请求未到主机；补映射 8095:8095，两个端口均可本地访问 |
| 08:00 | 线上问题 | 用户登录后点“收藏夹”报 `ValueError: not enough values to unpack`；定位 `music/browse` 期望 `<instance>://<sub>`，但根 BrowseFolder 的 `path` 被写成裸 `favorites` | 修复：根 folder 用 `f"{instance}://{key}"`；真实账号验证收藏夹 3 个、默认收藏夹 188 首 |
| 09:20 | 安装体验 | 新增 `docs/INSTALL.md`、`scripts/install_venv.py`、`uninstall_venv.py`、`install_docker.sh` | 干净容器实测 `install_docker.sh`：provider 落盘 + 重启成功；venv 脚本用假包目录验证安装/卸载 |
| 09:24 | 文档 | README 增加“安装到自己的 MA”入口；INSTALL 覆盖 Docker 镜像/bind mount/venv/HAOS 限制 | 明确“不要 pip install provider”、真实包路径、segno 可选 |
| 09:49 | 发布 | 远端 `MartinMC97/bili-music-assistant-provider` 已创建（private）；替换 manifest/README 占位符；统一提交身份为 MartinMC97；重写历史后推送 `main` | 远端 HEAD = `611abf8`；10 个 commit；默认分支 `main` |
| 10:00 | 许可证 | 添加 Apache-2.0 `LICENSE` + `NOTICE`；`pyproject.toml` 增加 license/authors/urls；README 增加 License 段 | 与 MA 核心同协议，便于后续 upstream |
| 15:30 | CI | 新增 `.github/workflows/docker-publish.yml`：push main/tag/PR 触发，buildx 多架构推 GHCR | 首次 run `40285453478` 成功（4m22s），推送 `main` / `latest` / `sha-4cb9c12`，digest `sha256:3b0d…` |
| 15:36 | CI | 增加发布后 `docker buildx imagetools inspect` 校验 amd64/arm64；补充 GHCR 拉取文档 | 第二次 run `40286074884` 成功，manifest 校验 amd64/arm64 通过；随后加 paths-ignore/手动输入 |
| 15:38 | CI | 第三次 run `40286588998` 成功（3m15s），manifest 校验 amd64/arm64 通过；最终 tags `main` / `latest` / `sha-4bf4090`，digest `sha256:96928a9f2925f8d91445070cb7d7542c137232a5726f9214483da644e05d1a17` | paths-ignore 生效，后续 docs-only 不再触发构建 |
