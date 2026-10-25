# parsers 模块完成说明

负责人：parsers
文件：`provider/bilibili/parsers.py`、`tests/unit/test_parsers.py`

## 完成内容

按 `docs/contracts.md` 实现了全部接口：

- `clean_title` / `parse_duration` / `to_https` / `extract_em_tags`
- `parse_search_item` / `parse_view` / `parse_fav_item` / `parse_fav_folder`
- `parse_music_center_item` / `parse_ranking_item`

映射要点：

- `Track.item_id`：普通稿件 `bvid`；`parse_view` 多 P 为 `bvid#cid`（取 `pages[0].cid`）；
  音乐中心为 `bvid#cid`，无顶层 `bvid` 时用 `related_archive` 的 `bvid#cid`，只有 aid 时
  用 `av<aid>#<cid>`。
- `Track.provider` / 所有 `ItemMapping.provider` / `MediaItemImage.provider` 均为
  `provider_instance`。
- `artists`：`ItemMapping(item_id=str(mid), provider=provider_instance, name=author)`；
  `mid` 缺失时退化为 author 文本作为 item_id。
- 封面：`//` 和 `http://` 都归一为 `https://`，`ImageType.THUMB`、
  `remotely_accessible=True`。
- `metadata.links`：`LinkType.WEBSITE` 的
  `https://www.bilibili.com/video/<bvid>`；只有 aid 时用 `.../video/av<aid>`。
- `metadata.description`：保留 API 原始标题（见下方偏差说明）。
- `metadata.popularity`：搜索/榜单用 `play`，详情用 `stat.view`，音乐中心优先
  `score`、回退 `related_archive.vv_count`；缺失为 `None`。
- `pubdate` → `metadata.release_date`（UTC aware）；收藏夹 `fav_time` → `date_added`。
- `ProviderMapping(item_id=item_id, provider_domain="bilibili",
  provider_instance=..., available=True, url=视频页)`。
- `parse_fav_folder`：`Playlist(item_id=f"fav:{id}", name=清洗后的标题,
  owner=owner_name, is_editable=False, provider=..., provider_mappings={...})`。
- `parse_fav_item`：`attr not in (None, 0)` 直接返回 `None`。
- 四个列表解析器（search/fav_item/music_center/ranking）缺关键字段返回 `None`，
  不抛异常；测试覆盖 `{}`、缺 bvid、缺 title、`None` 等情况。

## 测试与 lint

```text
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python -m pytest tests/unit/test_parsers.py -q
36 passed in 2.55s

docker exec -w /workspace bilibili-ma-dev /app/venv/bin/ruff check provider/bilibili/parsers.py tests/unit/test_parsers.py
All checks passed!

# 全量（含其他 agent 已加入的测试）
docker exec -w /workspace bilibili-ma-dev /app/venv/bin/python -m pytest -q
62 passed in 4.77s
```

测试只读 `tests/fixtures/*.json` 和手工构造的 dict，不联网；没有改
`docs/contracts.md` / `docs/implementation-plan.md`，没有 `git add/commit`。

## 与契约的偏差 / 需要 lead 知道的决策

1. **`metadata.description` 保存原始字符串（含搜索高亮 `<em class="keyword">`）**。
   依据契约“保留原始标题到 `metadata.description`”；`Track.name` 仍是清洗后的标题。
   如果 MA 客户端把 description 当纯文本展示，会看到 `<em>` 标签，需要的话可以改成
   `extract_em_tags(title)`，改动点只在 `_build_track` 一行。
2. **`clean_title` 对 `【...】` 不是无条件删除内容**：含噪声词（`NOISE_PATTERNS`、
   `官方`、`MV`、`无损`、`4K` 等）的标签整体删除；不含噪声的标签只去掉括号、保留内容
   （例如 `循环歌单|【八方来财】` → `循环歌单|八方来财`）。此外补了一小组局部噪声规则
   （分辨率 `2160P/4K`、`修复版`、紧跟中文的 `MV/PV`），这些不在
   `constants.NOISE_PATTERNS` 里。
3. **音乐中心 `cid` 不跨稿件混用**：顶层 `bvid` 只配顶层 `cid`，`related_archive`
   的 `bvid` 只配 archive 的 `cid`；fixture 中两者是不同视频，混用会导致播放错集。
   顶层有 bvid 但无 cid 时 item_id 只用 bvid，交给 provider 的 `view()` 补 cid。
4. **音乐中心的 artist mid**：音乐中心条目没有歌手 mid，使用 `author` 作为名称、
   `related_archive.uid`（UP 主）作为 `ItemMapping.item_id`；语义上是“可回链的 B 站
   用户”，不是严格的歌手 id。
5. **`parse_view` / `parse_fav_folder` 的异常行为**：两者返回类型不是 Optional
   （`Track` / `Playlist`），无法按“缺字段返回 None”处理。`parse_view` 在既无 bvid
   也无 aid 时抛 `ValueError`；`parse_fav_folder` 在缺 `id` 时抛 `ValueError`。
   正常 API 响应不会触发。catalog 若想完全防御，应在调用处兜底。
6. `parse_view` 多 P 的 `duration` 取 `pages[0].duration`，与 `bvid#cid` 指向的第一 P
   保持一致；单 P 用顶层 `duration`。
7. `parse_fav_folder` 的 `ProviderMapping.url` 使用
   `https://www.bilibili.com/medialist/detail/ml<id>`（收藏夹 API 不返回 mid，无法拼
   space 链接）。
8. `parse_duration` 对非法值返回 `0`（而不是抛异常或 -1），与 `Track.duration` 默认值
   一致；支持 `h:mm:ss` / `m:ss` / 纯数字字符串 / int / float。
