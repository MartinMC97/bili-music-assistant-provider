# 推送前安全检查

检查时间：2026-10-26
检查范围：工作区、全部 git 历史、测试 fixture、文档、Docker/安装脚本。

## 结论

**没有发现会被直接利用的凭证泄漏。** 没有提交过 B 站 Cookie、SESSDATA、JWT、API key 或密码；本地 dev token 文件从未进入 git；测试 fixture 里的音频 URL 已脱敏为 `*.example.invalid`。

仓库里剩余的是**占位符和开发环境信息**，推送前按下面清单处理即可。

## 已处理

| 项目 | 处理 |
|---|---|
| 内部主机名 | 从文档中移除，改为“现有生产 MA” |
| 本地示例口令 | 从安装文档中移除，改为通用提示 |
| 评审报告里的本地 token 文件描述 | 改为“未跟踪的本地 dev token 文件”，不包含路径/内容 |
| `manifest.json` 的 codeowner / documentation | 改为明确的 GitHub 占位符，推送前替换 |
| git 历史中的上述两项 | 已用 `git filter-branch` 重写全部 commit，并清理旧对象 |

## 扫描结果

| 检查项 | 结果 |
|---|---|
| `SESSDATA=...`、`bili_jct=...`、`DedeUserID=...` 真实值 | 未发现；只有代码拼接和测试占位值 `sess/jct/42` |
| JWT / Bearer token / `eyJ...` | 未发现 |
| 私钥 / `.pem` / `.key` / `.env` | 未发现，也没有被跟踪 |
| 内网 IP（192.168/10./172.16-31/100.x） | HEAD 与历史均未发现 |
| 邮箱、真实 B 站 mid、实例 id | 未发现（测试用 `42` 等占位） |
| 本地绝对路径（`/home/...`） | 未发现 |
| `.dev-data` / `artifacts/token.txt` | 未跟踪；`.gitignore` 覆盖 |
| 测试 fixture 中的 CDN 签名 URL | 已替换为 `cdn.example.invalid` / `backup.example.invalid` |
| Docker/安装脚本 | 只有占位路径和容器名，无凭证 |

## 运行时安全现状

- B 站凭据只存在 MA 的 `setup_data`，由 MA 加密落盘；手填 SESSDATA 使用 `SECURE_STRING`。
- `BilibiliCredential` 的敏感字段设置了 `repr=False`，不会因日志打印对象而泄漏。
- provider 日志只输出 endpoint/host，不输出完整带签名的音频 URL 或 Cookie。
- 媒体只流式播放，不落盘、不缓存音频。
- `docker/compose.dev.yml` 的 `LOG_LEVEL: debug` 仅适合本地；生产建议 `info`。

## 推送前还要做的（人工）

1. 替换 `provider/bilibili/manifest.json` 里的 `@your-github-handle` 和 documentation URL。
2. 决定并添加 `LICENSE`（当前仓库没有协议文件）。
3. 仓库名/描述/README 里的 `<this-repo-url>` 换成真实地址。
4. 在 GitHub 仓库设置里开启 **Secret scanning** 和 **Push protection**。
5. 不要把 `.dev-data/`、本地 token、真实 Cookie、`artifacts/token.txt` 提交进去。
6. 如果要提 upstream PR，先确认 `codeowners`、`stage: alpha`、文档和 B 站 ToS 的合规表述。

## 复跑扫描

```bash
# 当前树
git grep -nIE "SESSDATA=[A-Za-z0-9]{12,}|bili_jct=[A-Za-z0-9]{12,}|eyJhbGci|192\.168\.|100\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|/home/"

# 全部历史
git rev-list --all | while read c; do
  git grep -I -n -E "eyJhbGci|SESSDATA=[A-Za-z0-9]{12,}|192\.168\." "$c" -- . 2>/dev/null
done

# 确认敏感文件未被跟踪
git ls-files | grep -Ei "token|\.env|\.pem|\.key|dev-data"
```

## 第三方说明

- WBI 签名参数置换表、接口路径属于 B 站公开前端协议；实现代码为本仓库编写。
- 标题噪声词/清洗思路参考了 BiliMusic（该仓库当前没有 LICENSE 文件）；如果后续要正式分发，建议在 README 里注明参考来源并确认授权。
