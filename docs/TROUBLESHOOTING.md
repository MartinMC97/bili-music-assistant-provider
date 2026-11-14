# 故障排查

## 2.10.x 镜像启动即崩溃：`sqlite3.OperationalError: no such column: favorite`

### 症状

容器启动后立刻退出并反复重启，日志里是：

```text
Starting Music Assistant Server (...) version 2.10.11
...
sqlite3.OperationalError: no such column: favorite
  File ".../music_assistant/controllers/music/database.py", line ..., in __create_database_indexes
    await self.database.execute(
        f"CREATE INDEX IF NOT EXISTS {db_table}_favorite_idx on {db_table}(favorite);"
    )
Uncaught exception
```

日志里往往还能看到（或者更早的启动里看到过）：

```text
Migrating database from version 71 to 58
```

### 原因

MA 的 library 数据库 schema 版本在 2.10 和 2.11 之间不兼容：

| 版本 | `DB_SCHEMA_VERSION` | favorites 的存储方式 |
|---|---|---|
| 2.10.x（含 2.10.11） | 58 | 每张媒体表有 `favorite` 列 |
| 2.11.x（含 2.11 beta） | 71 | 独立的 `favorites` 表，媒体表上的 `favorite` 列被移除 |

2.10.x **没有到旧版本的回退迁移**：拿到 version=71 的库时，迁移函数里所有 `if prev_version <= N` 步骤全部跳过，然后无条件把版本号写成 58，最后创建 `*_favorite_idx` 索引时因为列不存在而崩溃。

更糟的是版本号在崩溃前已经被改成 58，所以**下一次启动会跳过迁移直接崩在同一处**，形成无限重启循环。

### 什么情况会踩到

- 曾经用 `latest` 或 `main` 标签跑过 MA。2026-11-08 ~ 11-13 期间这两个标签指向 **2.11.0b8 beta** 镜像（`main` 一直指到 2026-11-14 才被改回稳定版），期间拉取镜像启动就会把 `/data/library.db` 迁移到 schema 71；
- 之后再切回 2.10.x（包括 `2.10.11-bili-*`）时就会启动失败。

镜像本身没问题：用**全新的空白数据目录**启动 2.10.11 镜像是正常的，问题只出在版本不匹配的 `/data/library.db`。

### 修复

先停止容器，再按需选下面一种。所有命令都把 MA 的数据目录挂到 `/data`（按你的 compose 实际路径替换）。

#### 方案 A：保留数据，补回 `favorite` 列（会丢 favorites 标记）

```bash
# 1. 停容器
docker compose stop music-assistant

# 2. 检查（只读，会告诉你缺哪些列）
docker run --rm --entrypoint /app/venv/bin/python \
  -v /path/to/ma/data:/data \
  -v "$PWD/scripts:/scripts:ro" \
  ghcr.io/martinmc97/music-assistant:2.10.11-bili-dcf5c60 \
  /scripts/repair-library-schema.py --check /data/library.db

# 3. 修复（会先自动备份 library.db，再给 8 张表补 favorite 列）
docker run --rm --entrypoint /app/venv/bin/python \
  -v /path/to/ma/data:/data \
  -v "$PWD/scripts:/scripts:ro" \
  ghcr.io/martinmc97/music-assistant:2.10.11-bili-dcf5c60 \
  /scripts/repair-library-schema.py --repair /data/library.db

# 4. 启动
docker compose up -d
```

说明：

- 曲库、播放历史、本地歌单等数据都保留；
- 2.11 里收藏标记存在独立的 `favorites` 表，旧版不会读它，所以升级后 favorites 标记会是空的（对 B 站 provider 影响很小，收藏夹本身在 B 站侧）；
- 如果启动后仍报错，退回方案 B。

#### 方案 B：重置 library 数据库（最简单）

```bash
docker compose stop music-assistant
docker run --rm --entrypoint /app/venv/bin/python \
  -v /path/to/ma/data:/data \
  -v "$PWD/scripts:/scripts:ro" \
  ghcr.io/martinmc97/music-assistant:2.10.11-bili-dcf5c60 \
  /scripts/repair-library-schema.py --reset /data/library.db
docker compose up -d
```

`--reset` 只是把 `library.db` / `-wal` / `-shm` 改名备份，MA 会新建一个 schema 58 的库。设置、登录态、provider 配置都保留，曲库会自动重新扫描（本地自建歌单会丢）。

#### 方案 C：继续用 2.11 beta 镜像

如果你的 `/data` 已经被 2.11 迁移并且想完整保留，就继续用 2.11 镜像（schema 71）：

```yaml
image: ghcr.io/martinmc97/music-assistant:2.11.0b8-bili-dcf5c60
```

注意 2.11 目前仍是 beta。

### 预防

1. **不要在不同 MA 大版本之间共用同一个 `/data`**（尤其 2.10 ↔ 2.11）。测试 beta 镜像时用独立目录或独立 compose 项目；
2. 升级前备份数据目录（至少 `library.db`、`settings.json`、`auth.db`）；
3. 生产环境尽量固定版本 tag（如 `2.10.11-bili-dcf5c60`），不要用 `latest`/`main` 追新。
