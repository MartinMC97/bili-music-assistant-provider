# notes-catalog — 收藏夹 / 推荐 / browse

负责人：catalog
完成文件（仅这些）：

- `provider/bilibili/catalog.py`
- `tests/unit/test_catalog.py`

## 摘要

### `catalog.py`

严格按 `docs/contracts.md` 的签名实现：

- `iter_favorite_playlists(client, provider_instance)`
  - 匿名（`client.credential.has_login is False`）直接 `return`，不发任何请求；
  - 登录但 `mid` 不可用时也只 debug 后返回；
  - `client.fav_folders(mid)` 取自建收藏夹，`client.fav_collected(mid, page, 20)` 取订阅收藏夹；
  - 订阅夹按 `has_more` 翻页（`MAX_COLLECTED_PAGES=50` 保护），自建 + 订阅按 `item_id` 去重，自建优先；
  - 每个条目走 `parsers.parse_fav_folder`，坏条目（缺 id / 非 dict）跳过，不中断列表；
  - 订阅夹 owner 取响应里的 `upper.name` / `owner.name`；自建夹响应没有用户名字段，owner 留空（见“偏差/观察”）。
- `get_favorite_playlist(client, provider_instance, playlist_id)`
  - 解析 `fav:<id>`（失败抛 `MediaNotFoundError`，且不产生请求）；
  - 遍历上面的收藏夹列表匹配 `item_id`；找不到也抛 `MediaNotFoundError`。
- `get_favorite_tracks(client, provider_instance, playlist_id, page=0)`
  - `page` 为 0 基，请求 `client.fav_items(media_id, page + 1, 20)`，只取一页；
  - 逐项走 `parsers.parse_fav_item`；`attr != 0`、缺 bvid/title、解析抛异常都跳过；
  - `position = page * 20 + 1` 起，只对成功返回的 Track 连续递增（被跳过的失效项不占 position）；
  - 结果 `list[Track]`。
- `recommendation_rows(provider_instance)`
  - 固定三行：`rank_music`（音乐综合榜）、`new_music`（新歌）、`region_music`（分区榜）；
  - 每个 `RecommendationFolder` 都带 `item_id/provider/name/icon`；icon 用 MDI 名
    `mdi-chart-line` / `mdi-new-box` / `mdi-map-music`。
- `recommendation_items(client, provider_instance, row_id)`
  - `rank_music` → `client.music_rank()` + `parse_music_center_item`；
  - `new_music` → `client.new_music()` + `parse_music_center_item`；
  - `region_music` → `client.ranking_region(rid=3)` + `parse_ranking_item`；
  - 返回 `UniqueList[Track]`，天然按 item 去重；坏条目/解析异常跳过；未知 row_id 抛 `MediaNotFoundError`。
- `browse_items(client, provider_instance, path)`
  - 根路径 `""`、`"bilibili://"`（也接受首尾空白/斜杠）返回 3 个 `BrowseFolder`：
    `rank` / `new` / `favorites`，`path` 分别是 `rank` / `new` / `favorites`；
  - `rank` → `music_rank()` 曲目；`new` → `new_music()` 曲目；`favorites` → 收藏夹列表；
  - 也接受 `bilibili://rank` 这种完整 URI；未知路径抛 `MediaNotFoundError`。

所有 dict → MA 模型都走 `parsers` 里的公开函数（`parse_fav_folder` / `parse_fav_item` /
`parse_music_center_item` / `parse_ranking_item`），catalog 不手搓 Track/Playlist。
provider 内部全部相对 import，没有 import `music_assistant.models.media_capabilities`。
失败条目统一 `logger.debug(..., exc_info=True)` 后跳过；日志不含 Cookie/完整 URL。

### `test_catalog.py`

使用纯 fake client（`FakeClient`）+ `tests/fixtures/{fav_folders,fav_items,music_rank,new_music,ranking_region}.json`，
不联网。覆盖：

- 收藏夹映射：自建 + 订阅、去重、owner、provider、`is_editable=False`；
- 订阅夹 `has_more` 跨页；
- 匿名收藏夹为空且 0 次请求；
- 坏收藏夹条目跳过；
- `fav:<id>` 各种非法格式（空、无前缀、非数字、负数、多冒号、None）→ `MediaNotFoundError`，且不发请求；
- 收藏夹不存在 → `MediaNotFoundError`；
- 失效稿件 `attr != 0` 跳过；
- position：第 0 页 `[1,2]`，第 1 页 `[21,22]`（请求 `pn=2`）；
- 单条 parser 抛异常不中断整页；
- 推荐行 id/name/icon 与 provider；
- 三行推荐内容分别走 `music_rank` / `new_music` / `ranking_region(rid=3)`；
- 推荐项坏数据/重复项跳过与去重，未知 row 报 `MediaNotFoundError`；
- browse 根路径 3 个 BrowseFolder（两种根写法）、`rank`/`new` 曲目、`favorites` 收藏夹、匿名空、未知路径报错。

## 测试结果

```text
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/ruff check \
    provider/bilibili/catalog.py tests/unit/test_catalog.py
All checks passed!

docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python -m pytest tests/unit/test_catalog.py -q
28 passed in 2.31s

docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python -m pytest tests/unit -q
102 passed in 4.77s
```

未执行 `git add` / `git commit`；只新增了上面两个文件，未改其他人的模块、`docs/*`、`docker/*`、`tests/fixtures/*`。

## 与契约的偏差 / 补充约定

没有改变契约签名的偏差；以下是契约未写死、由实现补的行为：

1. **订阅收藏夹翻页**：契约的 catalog 段只写了“`pn` 从 1，`has_more` 继续”，实现里对
   `fav_collected` 也按 `has_more` 翻页（最多 50 页）。因此调用是
   `client.fav_collected(mid, page, 20)`，不是单参数；真实 client 签名支持这两个参数。
2. **position 的“连续”**：只对成功返回的 Track 连续编号（失效项不占号），所以同一页内
   position 无空洞；跨页仍按契约用 `page * 20` 定位起点。
3. **未知 id / 路径**：`get_favorite_playlist` 找不到、未知 recommendation row、未知 browse
   path 均抛 `MediaNotFoundError`（契约只明确了非法 id 的异常，这里沿用同一异常类型）。
4. **browse 路径容错**：除契约的 `""` / `"bilibili://"` / `rank|new|favorites` 外，也接受
   `bilibili://rank`、`/rank/` 这类写法；返回值里 BrowseFolder 的 `path` 用裸子路径
   `rank|new|favorites`，方便下一次 `browse()` 直接使用。
5. **自建收藏夹 owner**：B 站 `fav/folder/created/list-all` 不返回用户名，catalog 也拿不到
   登录用户名（函数签名只有 client/provider_instance），所以自建夹 owner 留空；订阅夹用
   `upper.name`。若需要展示“我”，建议 provider 层从 `nav().uname` 拿名字并提供给 catalog，
   但当前契约签名没有这个参数。

## 跨模块观察 / 建议（不在我文件范围，未修改）

1. **`new_music` 的 `cid: "0"`**：`tests/fixtures/new_music.json` 里每项 `cid` 都是字符串
   `"0"`，`parsers.parse_music_center_item` 会把它当有效 cid，生成
   `BV1mFam68EWm#0` 这样的 `item_id`。若 provider 的 `get_stream_details` 把 `#0` 当真实
   cid 去 `playurl`，播放会失败。建议 parser 侧把 `cid in ("", "0", 0)` 视为无 cid
   （或 provider 侧把 cid=0 当无 cid 回退 `view()`）。推荐行本身映射/去重是正确的。
2. **综合榜失效稿件**：实现计划 §3.8 提到综合榜顶层 `bvid` 可能失效、应回退
   `related_archive.bvid`；当前 `parse_music_center_item` 只在顶层缺 bvid 时才用 archive，
   顶层存在但失效时不会替换。catalog 按契约只调用 parser，未在 catalog 内做二次修正。
3. **凭据失效**：catalog 不吞 `BiliApiError`。登录态过期时 `fav_folders` 可能返回
   `code=-101`，会向上抛 `BiliApiError`。如果希望“同匿名、warning 一次”，需要 provider
   层捕获 `BiliApiError(code=-101)`，或后续在 catalog 里加窄捕获；当前实现按
   “provider 层负责翻译 MA 异常”的分工处理。
