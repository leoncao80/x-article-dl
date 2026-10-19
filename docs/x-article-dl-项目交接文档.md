# x-article-dl 项目交接文档

> 交接时间：2026-10-19 18:05（北京时间）
> 交接原因：Muse 自身 token 预算仅剩约 10%，后续工作交由接手模型继续
> 阅读对象：接手本项目的模型（或用户本人）

---

## 1. 项目一句话

**x-article-dl**：批量下载 X（Twitter）博主的全部 Articles 并转成高质量 Markdown 的开源工具。纯调 X 私有 GraphQL API，零浏览器。已验证：`@eng_khairallah1` 实测 112 篇全量下载成功。

## 2. 已定决策（用户拍板，不可改）

- 项目名：`x-article-dl`
- 开源协议：MIT，LICENSE 作者 `leoncao80`
- GitHub repo 建在 `leoncao80` 名下（**repo 尚未创建**）
- 维护策略：CI 每日冒烟测试 + `sync` 命令自愈 + 抄 gallery-dl 作业
- PyPI 发布延后（不上线计划本次范围）
- 分工原则：指挥+验收在人/上游，执行在机器端

## 3. 当前进度（精确）

- **阶段一（重构）进行中，未完成**：VM 上的 pi（`deepseek-flash`）正在把三个脚本重构成标准包
  - 设计文档已完成：`xadl-design.md`（650 行，模块划分与接口定义），已从 `/tmp` 搬到 `~/workspace/x-article-dl-handoff/`（持久位置）
  - 已写好的模块（在 `~/workspace/x-article-dl-handoff/xadl-staging/`）：`auth.py`、`listing.py`、`_endpoints.py`、`errors.py`、`__init__.py`，另有 `LICENSE`、`pyproject.toml`
  - 待写：`download.py`、`converter.py`、`cli.py`（converter 任务已发给 pi，含 8 项自测 + C1–C12 自查清单）
  - **NEC7 上 `/home/leon/HDD/sync_box/software/muse/x-article-dl/` 目录尚未创建**（18:05 核实不存在）；staging 还在 VM 本地，同步到 NEC7 是阶段一收尾动作
- **阶段二（本地验证）未开始**：`pip install -e .` + `x-article-dl eng_khairallah1 --limit 2`
- **阶段三（GitHub 上线）未开始**：建空 repo → push → v0.1 tag → Release → Actions 定时冒烟测试
- 协调子智能体已按用户要求关闭（省 token）；pi 进程随之终止，但**会话文件保留，可恢复**：
  `~/.pi/agent/sessions/--home-hatch--/2026-10-19T09-50-43-946Z_x-article-dl-p1.jsonl`（session-id `x-article-dl-p1`）

## 4. 源码现状（只读，不要改原文件）

| 位置 | 说明 |
|---|---|
| NEC7 `/home/leon/HDD/sync_box/software/muse/x-scraper/` | 原始脚本：`list_article_links.py`、`fetch_article_api.py`、`fetch_user_articles.py`，另有 `x_cookies.txt`（登录态）、`README.md`、`x_tweet_graphql.py`、`fetch_x.py`（三档降级通用抓取）、`x_browser_fetch.py`（旧版，已废弃） |
| VM `~/workspace/x-scraper/` | 上述目录的镜像副本，可直接读 |

三个脚本的分工（重构时按此拆模块）：
- `list_article_links.py` — 调 `UserArticlesTweets` 翻页，列出某博主全部 article 推文链接
- `fetch_article_api.py` — 调 `TweetResultByRestId`（`withArticleRichContentState=true`）逐篇下全文，自己解析 `content_state.blocks` 转 markdown
- `fetch_user_articles.py` — 一键全套，输出 `index.md` + 每篇 `article.md`/`img/`

## 5. 技术坑点全清单（重构时一条不许丢）

### 5.1 文章列表（listing）
- 接口：GraphQL `UserArticlesTweets`，**queryId = `G9ehBUadUrsNfz-XnAcV4A`**（从网页抓包得到）
- 请求头必须带 `x-twitter-auth-type: OAuth2Session`，否则 401
- cursor 翻页：`entryId` 形如 `cursor-bottom-xxx`（带后缀）；空页终止，防止死循环
- API 方式比 Chrome 滚动更全：`@eng_khairallah1` 实测 API 112 篇 vs Chrome 滚动 56 篇（虚拟列表会提前"稳定"漏一半）

### 5.2 单篇下载（download）
- 接口：GraphQL `TweetResultByRestId`，`fieldToggles` 必须带 `withArticleRichContentState=true`
- 直接拿 article 的 `content_state.blocks`（Draft.js 结构），自己转 Markdown

### 5.3 转换（converter）——核心优势，不许退化
- 标题分级必须保留：`header-one/two/three(/four)` → `#`/`##`/`###`（`/####`）
- 列表、链接、图片全部保留；`blockquote` 每行加 `> `
- `entityMap` 可能是 **list 形态**（不是 dict），解析时要兼容，否则链接丢失
- 行内样式 BOLD/ITALIC/LINK 正确渲染；atomic 图片块产出 `![](url)` 占位
- 未知 block 类型降级为段落并打 warning
- 输出不变量：每块产出以 `\n` 结尾的片段，最终 `"\n".join(parts).strip() + "\n"`
- 对比：gallery-dl 的 HTML 转换会丢 `header-two`，自研 converter 是本项目的核心优势
- 自测要求（pi 任务中已定义）：手造 `content_state` 样本，8 项断言——四级标题/嵌套列表/有序列表连续编号/blockquote/LINK 行内/atomic 图片占位/list 形态 entityMap/未知类型降级

### 5.4 认证（auth）
- Bearer 从 **gallery-dl 的 `twitter.py` 源码实时读取**（注意：源码里是**多段字符串拼接**，正则必须拼全；第一版只抓第一段导致 guest token 401）
- guest token 获取走 NEC7 的 `http://127.0.0.1:7897` 代理
- 登录 cookie：`x-scraper/x_cookies.txt`

### 5.5 网络
- **所有 X 请求必须在 NEC7 上经 `http://127.0.0.1:7897` 代理**；VM 本机直连 `api.x.com` 被拦截
- 已验证不可用：fxtwitter（CF 挑战）、xcancel（停服）
- `t.co` 短链须用 curl 取 `redirect_url`（urllib 回 404）

### 5.6 CLI
- `x-article-dl <handle> [--outdir DIR] [--limit N]`
- 输出：`<outdir>/index.md` 总目录 + 每篇 `<tweet_id>/article.md` + `img/` 图片

## 6. 目标包结构（阶段一交付物）

```
x-article-dl/                          # NEC7: /home/leon/HDD/sync_box/software/muse/x-article-dl/
├── src/x_article_dl/
│   ├── __init__.py
│   ├── auth.py        # Bearer / guest token / cookies
│   ├── listing.py     # UserArticlesTweets 翻页
│   ├── download.py    # TweetResultByRestId
│   ├── converter.py   # blocks → markdown
│   └── cli.py         # argparse 入口
├── pyproject.toml     # console entry: x-article-dl = x_article_dl.cli:main
├── README.md          # 用法说明
├── LICENSE            # MIT，作者 leoncao80
└── .gitignore
```

## 7. 环境与路径

- **NEC7**：`leon@100.111.53.1`（Tailscale）；SSH 脚本 `~/workspace/bin/ssh-to-leon '<命令>'`（用法同 ssh，密钥与隧道代理已配好）
- **VM pi**：`~/.npm-global/bin/pi`；**必须用 `script -qec` 包一层伪终端**（无 TTY 会挂死）；`-p` 非交互模式；`--model deepseek-flash`（用户付费 DeepSeek key，配在 `~/.pi/agent/models.json`，每晚 22:00 cron 从 NEC7 同步）；atria 免费渠道作备用
- 用户规则：NEC7 本地跑的脚本一律放在 `/home/leon/HDD/sync_box/software/muse/<项目>/`（本地盘 `/dev/sda`，不是共享盘）；原脚本不动、重构另起目录

## 附带产物

> 本节全部内容已于 2026-10-19 18:12（北京时间）同步到 NEC7：`/home/leon/HDD/sync_box/software/muse/x-article-dl-handoff/`（含本文档）。

| 路径 | 内容 |
|---|---|
| `~/workspace/x-article-dl-handoff/xadl-design.md` | pi 写的 650 行设计文档：模块划分、接口定义、坑点对照 |
| `~/workspace/x-article-dl-handoff/xadl-staging/` | 阶段一已写好的部分包代码：`auth.py`、`listing.py`、`_endpoints.py`、`errors.py`、`__init__.py` + `LICENSE`、`pyproject.toml` |
| `~/.pi/agent/sessions/--home-hatch--/2026-10-19T09-50-43-946Z_x-article-dl-p1.jsonl` | pi 会话记录（`--session-id x-article-dl-p1` 可恢复） |

注意：如果接手模型不在本 VM 上，需要自行解决两件事：① NEC7 的 SSH 访问（`~/workspace/bin/ssh-to-leon` 只在这台 VM 上配好）；② 所有 X 请求必须经 NEC7 的 `127.0.0.1:7897` 代理发出。

## 8. 接手操作指南

### 方案 A（推荐）：恢复 pi 会话继续阶段一
```bash
script -qec "$HOME/.npm-global/bin/pi -p '汇报 x-article-dl-p1 会话当前进度：设计文档是否完整、converter.py 写到哪、NEC7 上建了哪些文件' --model deepseek-flash --session-id x-article-dl-p1" /dev/null
```
先让 pi 自查进度，再让它继续。注意：pi 进程已终止，恢复后让它先检查 NEC7 目标目录实际状态，避免重复劳动。

### 方案 B：新起会话
如果 pi 会话状态混乱，放弃恢复，按第 5、6 节从头让 pi 做（设计文档要点都在上面）。

### 阶段一验收标准
- 对照第 5 节坑点清单逐项核对，无遗漏、无退化（尤其标题分级）
- converter 8 项自测全部通过
- 包目录在 NEC7 目标路径下完整

### 阶段二（本地验证）
在 NEC7 上：`pip install -e .`（按 NEC7 实际 python 环境来），跑
`x-article-dl eng_khairallah1 --limit 2 --outdir /tmp/xadl-verify`，
检查：2 篇 article.md 生成、标题分级正确、图片下载成功、index.md 正常。

### 阶段三（GitHub 上线）
1. 在 GitHub `leoncao80` 名下建空 repo `x-article-dl`
2. push 代码 → 打 `v0.1` tag → 发 Release
3. 加 GitHub Actions 定时冒烟测试（每日跑 `--limit 1`，验证 X 接口未失效）

## 9. 工作约定（用户习惯）

- 时间一律北京时间并标注"北京时间"
- 回复用户用中文，简短直接；只答问的那一步，不替他扩题
- 动手任务默认直接做（经 SSH 在 NEC7 上），只在决策点找用户
- Token 成本是红线：重活走用户付费的 `deepseek-flash`，调度开销压到最小（2026-10-19 用户原话"你只有10%可用"）
- 不可逆动作（如下单、删数据）先摆出来等用户点头；GitHub 建库/push 用户已拍板，可直接做
