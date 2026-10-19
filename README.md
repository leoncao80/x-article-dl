# x-article-dl

[![ci](https://github.com/leoncao80/x-article-dl/actions/workflows/ci.yml/badge.svg)](https://github.com/leoncao80/x-article-dl/actions/workflows/ci.yml)
[![smoke](https://github.com/leoncao80/x-article-dl/actions/workflows/smoke.yml/badge.svg)](https://github.com/leoncao80/x-article-dl/actions/workflows/smoke.yml)

批量下载某个 X（Twitter）博主的**全部 Articles**，转成结构完整的高质量 Markdown。纯 Python 标准库，直连 X 私有 GraphQL API，**不需要浏览器**。

> Batch-download every X (Twitter) Article of a user as clean Markdown, via the private GraphQL API only. Zero runtime dependencies.

实测：`@eng_khairallah1` 112 篇全量下载成功（API 全量 vs Chrome 滚动仅 56 篇）。

## 1. 是什么 / 不是什么

**是**：一个仅用于**个人存档与研究**的命令行工具。输入一个 handle，输出该博主所有 Article 的 Markdown + 本地图片 + 索引。

**不是**：不是爬虫框架，不抓时间线普通推文，不做增量监控服务，不绕过任何付费/权限限制。

使用即表示你遵守 [X Terms of Service](https://x.com/tos)：**只下载你有权访问的内容**，控制频率，不要用于再发布他人作品。作者不对使用者的行为负责。

## 2. 原理

只用三条 GraphQL 请求（queryId 集中放在 `src/x_article_dl/_endpoints.py`，轮换时只改一处）：

| 用途 | operation | 要点 |
|---|---|---|
| 解析 handle → userId | `UserByScreenName` | 用 API 返回的权威大小写拼链接 |
| 列出全部文章 | `UserArticlesTweets` | 必须带 `x-twitter-auth-type: OAuth2Session`；cursor 翻页 |
| 取单篇全文 | `TweetResultByRestId` | `fieldToggles.withArticleRichContentState=true` 是拿正文的唯一开关 |

正文是 Draft.js 的 `content_state.blocks`（`header-two` / `list-item` / `blockquote` / `atomic` …），由 `converter.py` 纯函数直接转 Markdown，不经过 HTML。

认证从 **gallery-dl 的源码实时读取 Bearer**（不内置 token），会话 cookie 从浏览器导出的 Netscape 文件读取。

## 3. 与 gallery-dl 的差异

gallery-dl 走 HTML → Markdown，`header-two` 等标题层级会丢失，列表/引用的结构也不稳定。本项目直接解析 Draft.js blocks：

- `header-one/two/three(/four…six)` → `#` / `##` / `###`（**不丢级**）
- 有序/无序列表：连续合并、每层独立编号、`depth` 缩进
- `blockquote` 逐行加 `> `
- 行内 `Bold` / `Italic`、`LINK` 实体、`IMAGE` 原子块
- X 实际使用的 `MARKDOWN` / `TWEET` 原子实体（代码块、嵌入推文）原样保留
- 未知 block 类型降级为段落并记录 warning，不静默丢内容

## 4. 安装

```bash
pipx install .          # 推荐：隔离环境 + 全局命令
# 或
pip install -e .        # 开发模式
```

要求 Python ≥ 3.9，零第三方运行时依赖。

`gallery-dl` **可选**，仅作为 Bearer / queryId 的来源。没有 gallery-dl 时用 `--bearer TOKEN` 传入。

## 5. 前置条件

### 5.1 运行环境

- **Python 3.9+**（Linux / macOS / Windows 均可）
- 能访问 `api.x.com`、`x.com`、`pbs.twimg.com`
- **不需要浏览器、不需要 Node**；`gallery-dl` 可选（仅用来读 Bearer）

**网络 / 代理**：默认**直连**。只有当你的网络需要代理时（公司网络、云服务器、所在地区直连不通）才需要配置，任选一种：

```bash
x-article-dl <handle> --proxy http://127.0.0.1:7897      # 只对本次生效
export XADL_PROXY=http://127.0.0.1:7897                 # 当前 shell 生效
# Windows PowerShell：$env:XADL_PROXY="http://127.0.0.1:7897"
```

优先级：`--proxy` > `XADL_PROXY` > 标准环境变量 `HTTPS_PROXY` / `https_proxy` > 直连。需要强制直连时用 `--no-proxy`。

### 5.2 cookies（必需）

X 的文章列表接口只接受登录态，需要一个 **Netscape 格式**的 cookie 文件，且满足：

- 含 `auth_token` 与 `ct0`；
- 两者来自**同一个浏览器、同一个会话**（拼两个 profile 的 cookie 必定 403）；
- `auth_token` 是 HttpOnly，行首带 `#HttpOnly_`，**必须原样保留**。

**导出方法（任选）：**

| 方式 | 做法 |
|---|---|
| 浏览器扩展 | Chrome / Edge / Firefox 装一个能导出 Netscape 格式的扩展（如 “Get cookies.txt LOCALLY”），在 **x.com** 页面导出 |
| 手工 | DevTools → Application → Cookies → `https://x.com`，取 `auth_token` 和 `ct0`，按下面的行格式各写一行 |

行格式（字段用 TAB 分隔，`#HttpOnly_` 前缀不能丢）：

```
#HttpOnly_.x.com	TRUE	/	TRUE	0	auth_token	<值>
.x.com	TRUE	/	TRUE	0	ct0	<值>
```

**指定文件**（任选一种，路径按你的系统来）：

```bash
export XADL_COOKIES=~/x_cookies.txt        # Linux / macOS
# Windows PowerShell：$env:XADL_COOKIES="C:\Users\you\x_cookies.txt"

x-article-dl <handle> --cookies /path/to/x_cookies.txt
```

自动探测顺序：`--cookies` → `XADL_COOKIES` → `./x_cookies.txt` → `~/.config/x-article-dl/cookies.txt`。

> 🔒 cookie 等同于你的登录态：`.gitignore` 已排除 `x_cookies.txt` / `*.cookies.txt`，不要提交、不要贴到 issue 或公开 CI 日志。

## 6. 用法

```bash
x-article-dl eng_khairallah1                          # 全量
x-article-dl @eng_khairallah1 --limit 2 --outdir /tmp/xadl-verify
x-article-dl eng_khairallah1 --no-images --save-raw -v
```

| 选项 | 默认 | 说明 |
|---|---|---|
| `handle` | 必填 | 可带 `@` |
| `--outdir DIR` | `./out/<handle>_articles` | 输出目录 |
| `--limit N` | 0（全部） | 只取最新 N 篇，取够即停，不多翻页 |
| `--proxy URL` / `--no-proxy` | 直连 | 代理优先级：`--proxy` > `XADL_PROXY` > `HTTPS_PROXY` > 直连 |
| `--cookies FILE` | 自动探测 | Netscape cookies |
| `--bearer TOKEN` | 从 gallery-dl 读 | 应急逃生舱（列表接口仍需 cookies） |
| `--user-id UID` | 自动解析 | 跳过 `UserByScreenName`，省一次请求 |
| `--force` | off | 忽略已有 `page.json` 重下 |
| `--no-images` | off | 只出 Markdown，保留远端图片 URL |
| `--save-raw` | off | 保存完整 API 响应 `raw.json`（含敏感数据，勿外传） |
| `--json` | off | stdout 输出机器可读汇总（日志仍在 stderr） |
| `--delay SEC` | 1.0 | 篇间间隔；`--no-wait` 关闭所有等待 |
| `-q` / `-v` | INFO | `-q` 只打警告；`-v` 打调试细节 |

**退出码**：`0` 全部成功 · `1` 部分失败 · `2` 配置/认证错误 · `3` 全失败（列表为空或全部篇目失败）· `130` 用户中断。

**输出结构**：

```
<outdir>/
├── index.md                 # 总目录（标题/字数/图数/链接 + 失败清单）
└── <tweet_id>/
    ├── article.md           # YAML frontmatter + # 标题 + 正文（图片为本地相对路径）
    ├── page.json            # 元数据（可据此断点续传）
    ├── raw.json             # 仅 --save-raw
    └── img/img_00.jpg ...   # 封面 img_00，正文图 img_01 起
```

`chars` 统计的是**纯文本**字符数（不含 `##`、`[]()` 等 Markdown 语法）。

## 7. 故障排查

| 现象 | 原因 / 处理 |
|---|---|
| `HTTP 401` | 缺 `x-twitter-auth-type` 头或 cookie 无 `auth_token`；检查 cookies 文件 |
| `HTTP 403`（“Could not authenticate you”） | `ct0` 与 cookie 里的不一致，或会话已过期；重新导出同一浏览器的 cookies |
| `HTTP 429` | 触发限流；默认会等到 `x-rate-limit-reset` 再重试，或用 `--no-wait` 关闭等待 / 降低频率 |
| 列表为空 | `features` 缺字段或账号无 Articles（`UserArticlesTweets` 返回空）；`-v` 看服务端 message |
| `在 ... 里没找到 Bearer token` | gallery-dl 未安装或改了写法；用 `--bearer` 传入 |
| `Query not found` / 404 | queryId 轮换：只需改 `_endpoints.QUERY_IDS` 一处（`-v` 日志会回显实际 URL） |
| 连不上 `api.x.com` | 默认直连；如果本机需要代理，用 `--proxy` 或 `XADL_PROXY`；代理端口写错也会表现为超时 |
| 图片失败但文章成功 | 单图失败只记 warning 并保留远端 URL，不中断；`page.json` 里有记录 |
| `article 的 content_state.blocks 为空` | 该推文不是长文 Article，或 `withArticleRichContentState` 未生效 |

## 8. 隐私

- 不收集、不上报任何数据；无遥测。
- cookies / Bearer 只在本机内存与请求头中使用，不写日志（`__repr__` 打码）。
- `raw.json` 含完整 API 响应（可能包含账号信息），默认不写；`.gitignore` 已排除 `x_cookies.txt`、`*.cookies.txt`、`raw.json`、`out/`。

## 开发

```bash
python3 -m unittest discover -s tests -v    # 27 个单测，全部纯离线
```

模块划分与 52 条坑点清单见 `docs/xadl-design.md`。

## License

MIT © 2026 leoncao80
