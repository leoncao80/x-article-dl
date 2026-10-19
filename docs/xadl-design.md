# x-article-dl 重构设计文档（v1.0）

> 状态：**设计稿（只读研读产物，未改动任何源码）**
> 输入：`~/workspace/x-scraper/{list_article_links.py, fetch_article_api.py, fetch_user_articles.py}`
> 上下文参考：`x_tweet_graphql.py`、`x_browser_fetch.py`、`fetch_x.py`、`README.md`
> 目标产物：标准 Python 包 `src/x_article_dl/`，console entry `x-article-dl`
> 实测基线：@eng_khairallah1 共 112 篇（API 全量）vs Chrome 滚动 56 篇

---

## 0. 旧架构 vs 新架构

| 维度 | 旧（3 脚本 + 2 遗留抓取器） | 新（1 个包） |
|---|---|---|
| 入口 | 3 个 `main()`，靠 `sys.path.insert` 互相 import | 1 个 `cli.main()`，包内相对导入 |
| 认证 | `read_bearer()` 内部 `sys.exit()`；无缓存 | `auth.XClient`，异常上抛，Bearer 按 mtime 缓存 |
| 翻页 | `while True` + list 去重 O(n²) | 生成器 + dict 去重 + 四重终止 |
| 正文 | `fetch_one()` 既抓又写盘 | `fetch_article()`（纯数据）→ `write_bundle()`（落盘） |
| 转 MD | 单函数，靠字符串 replace 换图 | `converter` 纯函数层，返回结构化 `(body_md, media_refs)` |
| 浏览器 | Chrome/CDP + `body.innerText` 启发式解析（~700 行） | **全删**，API 直出 Draft.js blocks |
| 日志 | `print(..., file=sys.stderr)` 散落 | `logging` + 语义化退出码 |

新包**零第三方运行时依赖**（纯标准库）。`gallery-dl` 只被**读取源码文本**（取 Bearer / queryId），不 import、不作为依赖。

---

## 1. 目录结构与模块边界

```
x-article-dl/
├── pyproject.toml
├── LICENSE                 # MIT, Copyright (c) 2026 leoncao80
├── README.md
├── .gitignore
└── src/x_article_dl/
    ├── __init__.py         # __version__、公开 API 再导出
    ├── auth.py             # Bearer、cookies、proxy/UA、XClient 传输、guest token
    ├── listing.py          # UserByScreenName + UserArticlesTweets 翻页
    ├── download.py         # TweetResultByRestId + 图片下载 + bundle 落盘
    ├── converter.py        # Draft.js blocks → Markdown（纯函数，无 I/O）
    ├── cli.py              # argparse + 编排 + index.md + 退出码
    ├── _endpoints.py       # queryId / features / fieldToggles 常量 + build_graphql_url()
    └── errors.py           # 异常层级
```

`_endpoints.py` 与 `errors.py` 是内部模块：不扩大公共 API，但**集中放 queryId 是"轮换时只改一处"的前提**。若坚持只有 6 个文件，可并入 `auth.py`/`__init__.py`，代价是 auth 变杂物袋，**不推荐**。

### 1.1 异常层级（`errors.py`）

```python
class XadlError(Exception):
    exit_code: int = 1

class ConfigError(XadlError):          exit_code = 2   # 缺 cookies / 坏参数 / 找不到 gallery-dl
class AuthError(XadlError):            exit_code = 2   # 401/403、ct0 不匹配、Bearer 读不到
class NetworkError(XadlError):         exit_code = 1   # 代理不通、TLS、超时
class RateLimitError(NetworkError):    exit_code = 1   # 429，带 reset_at
class GraphQLAPIError(XadlError):      exit_code = 1   # data.errors[] 非空
class UserNotFoundError(XadlError):    exit_code = 1
class TweetUnavailableError(XadlError):exit_code = 1   # TweetTombstone / TweetUnavailable
class NotAnArticleError(XadlError):    exit_code = 1   # tweet 无 article 字段
class ArticleEmptyError(XadlError):    exit_code = 1   # blocks 为空
class ImageDownloadError(XadlError):   exit_code = 1   # 单图失败，默认降级不抛出
```

**铁律**：库代码禁止 `sys.exit()` / `print()`，一律 raise + `logging`。旧代码 `read_bearer()`、`load_cookies()`、`find_chrome()` 都在库里 `sys.exit`，必须替换（坑 A6）。

---

## 2. 模块详细设计（签名 + 返回 + 异常）

### 2.1 `auth.py` —— 认证与传输

```python
# ---- 常量 ----
DEFAULT_PROXY = "http://127.0.0.1:7897"
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
GALLERY_DL_CANDIDATES = (
    "~/.local/lib/python*/site-packages/gallery_dl/extractor/twitter.py",
    "~/.local/pipx/venvs/gallery-dl/lib/python*/site-packages/gallery_dl/extractor/twitter.py",
    "/usr/lib/python3*/site-packages/gallery_dl/extractor/twitter.py",
    "/usr/local/lib/python3*/dist-packages/gallery_dl/extractor/twitter.py",
)

@dataclass(frozen=True)
class Credentials:
    bearer: str
    auth_token: str | None
    ct0: str | None
    user_agent: str = DEFAULT_UA
    @property
    def has_session(self) -> bool: ...      # auth_token and ct0

@dataclass
class Limits:
    timeout: float = 30.0
    retries: int = 3
    backoff: float = 2.0
    respect_rate_limit: bool = True

def locate_gallery_dl_twitter(path=None) -> Path
    """顺序：显式 path → 已 import 的 gallery_dl.__file__ 同目录 → glob 候选（多命中按 mtime 新→旧）
    → importlib.metadata.files。失败 raise ConfigError。"""

def read_bearer(path=None, *, min_len: int = 50) -> str
    """提取 Bearer（多段拼接必须拼全，坑 A1）；返回不含 'Bearer ' 前缀、保留 %3D 的 token。"""

def read_query_id(name: str, path=None) -> str | None
    """从同一份源码取 '/i/api/graphql/<QID>/<name>'，供 UserByScreenName 兜底；找不到返回 None。"""

def default_cookie_path() -> Path
    """XADL_COOKIES → ./x_cookies.txt → ~/.config/x-article-dl/cookies.txt，取第一个存在者。"""

def load_cookies(path=None) -> dict[str, str]
    """Netscape 解析；必须处理 #HttpOnly_ 前缀（坑 A2）；过滤 domain 非 x.com、过期条目。
    缺 auth_token/ct0 → ConfigError。"""

def build_headers(creds: Credentials, *, session: bool = True,
                  guest_token: str | None = None) -> dict[str, str]
    """session → authorization / x-csrf-token=ct0 / x-twitter-auth-type: OAuth2Session /
    x-twitter-active-user: yes / x-twitter-client-language / User-Agent / Cookie: auth_token; ct0
    guest   → authorization / User-Agent (+ x-guest-token)"""

class XClient:
    def __init__(self, *, proxy: str | None = DEFAULT_PROXY, creds=None,
                 limits: Limits = Limits(), user_agent: str = DEFAULT_UA) -> None: ...
    def credentials(self, *, refresh: bool = False) -> Credentials: ...
    def guest_token(self, *, refresh: bool = False) -> str: ...
    def get_json(self, url: str, *, session: bool = True, guest: bool = False) -> dict: ...
    def get_bytes(self, url: str, *, referer: str | None = None) -> bytes: ...
    def graphql(self, op: str, variables: dict, *, features: dict | None = None,
                field_toggles: dict | None = None, session: bool = True,
                guest: bool = False) -> dict: ...
```

**行为约定**

- `proxy=None` → `ProxyHandler({})` **显式禁用环境代理**；urllib 默认 opener 会读 `http_proxy`，旧代码靠自建 opener 隐式规避，新代码要显式（坑 A9）。
- 重试：`NetworkError`/5xx/429 指数退避；429 且带 `x-rate-limit-reset` → sleep 到 reset（上限 60s，`--no-wait` 关闭）。
- `401/403` 不重试 → `AuthError`；`session=True` 时失败一次自动**重读 Bearer** 重试一次（gallery-dl 刚升级最常见），仍失败再抛。
- `graphql()`：`payload["errors"]` 非空 → `GraphQLAPIError`；`payload["data"]` 缺失 → 同上。
- Bearer 缓存键 `(path, mtime, size)`，gallery-dl 升级后自动生效。
- guest token 按实例缓存（X 的 guest token 是 IP 维度，约 3 小时）；403/429 → `refresh=True` 重试一次；**guest 请求不要带 cookie**（带 cookie 用 guest token 会 403）。
- 不打印 token，`__repr__` 打码。

---

### 2.2 `listing.py` —— 博主文章列表

```python
@dataclass(frozen=True)
class UserInfo:
    user_id: str
    screen_name: str        # 权威大小写，来自 API（拼推文 URL 用，坑 L6）
    name: str = ""

@dataclass(frozen=True)
class ArticleRef:
    tweet_id: str
    url: str                # https://x.com/<screen_name>/status/<tweet_id>
    sort_index: int         # 0-based，API 原始顺序（新→旧）

def resolve_user_id(client: XClient, handle: str) -> UserInfo
    """UserByScreenName。handle 去 '@'。QID 取 read_query_id("UserByScreenName")，兜底 _endpoints。
    raise UserNotFoundError / GraphQLAPIError / AuthError。"""

def iter_articles(client: XClient, handle: str, *, user_id: str | None = None,
                  page_size: int = 20, page_delay: float = 0.5,
                  max_pages: int = 200) -> Iterator[ArticleRef]
    """生成器，逐页 yield，边翻边下。
    终止：① 无 cursor-bottom；② 新 cursor 已在 seen（回环）；③ 本页新增 0（重复/空页）；
          ④ max_pages 硬上限（默认 200 页 = 4000 篇）。"""

def list_articles(client, handle, *, limit: int = 0, user_id=None, **kw) -> list[ArticleRef]
    """= list(islice(iter_articles(...), limit or None))。"""

def _parse_entries(payload: dict) -> tuple[list[str], str | None]
    """返回 (tweet_ids, next_cursor)。兼容 result.timeline.timeline.instructions 与
    result.timeline.instructions（坑 L8）；兼容 TimelineAddEntries / TimelineReplaceEntry /
    TimelinePinEntry / moduleItems 形态。entryId 前缀 'tweet-' → split('-',1)[1]；
    前缀 'cursor-bottom' → content.value（不可假设恰为 'cursor-bottom'）。"""
```

---

### 2.3 `download.py` —— 单篇抓取 + 落盘

```python
@dataclass(frozen=True)
class ImageAsset:
    index: int                    # 1-based → img_01.jpg
    media_id: str
    remote_url: str
    local_path: str | None        # 相对 bundle："img/img_01.jpg"；失败为 None
    is_cover: bool = False
    bytes: int = 0

@dataclass
class Article:
    tweet_id: str
    tweet_url: str
    article_id: str
    title: str
    author_handle: str
    author_name: str
    created_at: str | None        # ISO8601 UTC（坑 D12）
    body_md: str
    blocks_count: int
    images: list[ImageAsset]
    chars: int                    # **纯文本**字符数（坑 D10）
    plain_text: str
    warnings: list[str]
    fetched_at: str               # ISO8601 UTC
    raw: dict = field(repr=False, default_factory=dict)   # 仅 --save-raw

def fetch_article(client: XClient, tweet_id: str, *, use_guest: bool = True) -> Article
    """TweetResultByRestId + withArticleRichContentState=True（坑 D1）。不写盘、不下图。
    raise TweetUnavailableError / NotAnArticleError / ArticleEmptyError / GraphQLAPIError / NetworkError。"""

def parse_article_payload(payload: dict) -> dict
    """纯函数（可单测）：tweetResult.result → TweetWithVisibilityResults.tweet →
    article.article_results.result；校验 __typename；抽出 title/blocks/entityMap/
    media_entities/cover_media/rest_id。"""

def download_article(client: XClient, tweet_id: str, outdir: Path, *,
                     tweet_url: str | None = None, force: bool = False,
                     download_images: bool = True, save_raw: bool = False,
                     image_delay: float = 0.2) -> Article
    """fetch → convert → 下图 → 写 bundle。force=False 且 page.json 存在且 article.md 非空 → 直接复用（坑 D13）。
    单图失败仅 warning（坑 D14）。"""

def write_bundle(article: Article, bundle_dir: Path, *, save_raw: bool = False) -> Path
    """原子写：*.tmp → os.replace，避免半截文件。"""

def _download_image(client, url: str, dest: Path, *, referer: str) -> int
    """返回字节数。Content-Type 嗅探定扩展名（坑 D9）。<2048 字节视为占位图 → 删除并 raise。"""

def render_article_markdown(article: Article) -> str
    """YAML frontmatter + `# Title` + 正文（图片已是本地相对路径）。"""
```

**bundle 结构**

```
<outdir>/
├── index.md
└── <tweet_id>/
    ├── article.md
    ├── page.json          # tweet_id/article_id/title/chars/images/fetched_at/method
    ├── raw.json           # 仅 --save-raw
    └── img/img_01.jpg ...
```

**`article.md` 模板**

```markdown
---
title: "<title>"
tweet_id: "1234567890"
tweet_url: "https://x.com/<handle>/status/1234567890"
article_id: "9876543210"
author: "@<handle>"
created_at: "2026-10-17T08:12:00Z"
fetched_at: "2026-10-19T07:41:00Z"
chars: 12345
images: 6
source: x-article-dl v1.0.0
---

# <title>

<正文>
```

---

### 2.4 `converter.py` —— Draft.js blocks → Markdown（纯函数）

```python
SUPPORTED_BLOCKS = ("unstyled", "header-one", "header-two", "header-three",
                    "header-four", "header-five", "header-six",
                    "unordered-list-item", "ordered-list-item",
                    "blockquote", "atomic", "code-block")

def normalize_entities(content_state: dict) -> dict[str, dict]
    """X 的 entityMap 是 **list[{key,value}]**，转 dict 且 key 一律 str()（坑 C1）；兼容已是 dict 的旧格式。"""

def media_map_from(article_result: dict) -> dict[str, str]
    """media_entities[]: media_id → 首选 URL（original_img_url → preview_image → variants 最高码率），
    统一补 `?format=jpg&name=large`（坑 D7/D8）。"""

def render_inline(block: dict, entities: dict[str, dict], *, style: bool = True) -> str
    """先 inlineStyleRanges（BOLD→**x**、ITALIC→*x*），再 entityRanges LINK→[t](url)。
    重叠时以 entity 为主、内层套 style（坑 C5）。"""

def render_block(block: dict, entities: dict[str, dict],
                 media: dict[str, str] | None = None) -> list[str]
    """单块 → 若干 markdown 行；列表项按 depth 用两空格缩进（坑 C6）。"""

def blocks_to_markdown(blocks: list[dict], entities: dict[str, dict],
                       media: dict[str, str] | None = None
                       ) -> tuple[str, list[str], list[str]]
    """返回 (markdown_body, ordered_media_ids, warnings)。
    - **header-one/two/three 必须精确映射 #/##/###（核心优势，坑 C2）**；four..six → ####..######
    - 列表：连续同类合并、有序从 1 编号、depth 缩进、非列表块打断（坑 C3）
    - blockquote：**每行**加 '> '（坑 C4）
    - atomic：实体取 IMAGE/VIDEO/GIF 的 media_id，按 media_map 产出 ![](url) 占位；
      无 URL → warning 并跳过
    - 未知类型：有文本则按段落输出 + warning（不丢内容）"""

def convert_article(article_result: dict) -> tuple[str, list[tuple[str, str]], list[str]]
    """content_state → (body_md, [(media_id, url)], warnings)。
    标题与 blocks 首个 header-one 文本相同时去重该块（坑 C8）。"""
```

**输出不变量（必须保留）**：每块产出以 `\n` 结尾的片段，最终 `"\n".join(parts).strip() + "\n"`
→ 块之间天然隔一个空行。改这个 join 规则会破坏段落/列表识别（坑 C3）。

---

### 2.5 `cli.py` —— 命令行

```python
def build_parser() -> argparse.ArgumentParser
def main(argv: list[str] | None = None) -> int      # console_scripts 入口，返回退出码
def _run(args: argparse.Namespace) -> int           # 编排
def _write_index(outdir: Path, handle: str, results: list[Article],
                 failures: list[tuple[ArticleRef, str]]) -> Path
```

```
x-article-dl <handle> [--outdir DIR] [--limit N] [--proxy URL | --no-proxy]
              [--cookies FILE] [--bearer TOKEN] [--user-id UID]
              [--force] [--no-images] [--save-raw] [--json] [--no-wait]
              [-q|--quiet] [-v|--verbose] [--version]
```

| 选项 | 默认 | 说明 |
|---|---|---|
| `handle` | 必填 | 可带 `@`，内部 `lstrip("@")` |
| `--outdir` | `./out/<handle>_articles` | 不存在则创建 |
| `--limit N` | 0=全部 | **取最新 N 篇**（API 顺序新→旧，islice） |
| `--proxy` | `http://127.0.0.1:7897` | 优先级：`--proxy` > `XADL_PROXY` > `HTTPS_PROXY` > 默认 |
| `--no-proxy` | off | 直连（非 NEC7 本机调试） |
| `--cookies` | 自动探测 | Netscape 格式 |
| `--bearer` | 从 gallery-dl 读 | 应急逃生舱 |
| `--user-id` | 自动解析 | 跳过 UserByScreenName，省一次请求（坑 L9） |
| `--force` | off | 忽略已有 page.json 重下 |
| `--no-images` | off | 只出 markdown，图片保留远端 URL |
| `--json` | off | stdout 打机器可读汇总（日志仍在 stderr） |
| `--quiet` / `--verbose` | WARNING / INFO | `-v` 打印每页 cursor 与每篇耗时 |

退出码：`0` 全部成功；`1` 部分失败；`2` 配置/认证错误；`3` 全失败（列表为空或所有篇目失败）。

`index.md` 格式：

```markdown
# @<handle> 的 X Articles（112/112 篇）

- 抓取时间：2026-10-19 15:41 (+08:00)
- 工具：x-article-dl 1.0.0
- 来源：https://x.com/<handle>/articles

| # | 标题 | 字数 | 图 | 链接 |
|---|------|------|----|------|
| 1 | Title A | 12345 | 6 | [article.md](1234567890/article.md) |

## 失败清单
- 1234567891 — TweetUnavailable（https://x.com/.../status/1234567891）
```

---

### 2.6 `pyproject.toml` / `LICENSE` / `README.md` / `.gitignore`

```toml
[build-system]
requires = ["hatchling>=1.21"]
build-backend = "hatchling.build"

[project]
name = "x-article-dl"
version = "1.0.0"
description = "Download all X (Twitter) Articles of a user as clean Markdown, via private GraphQL API only."
readme = "README.md"
requires-python = ">=3.9"
license = { text = "MIT" }
authors = [{ name = "leoncao80" }]
keywords = ["x", "twitter", "articles", "markdown", "scraper", "graphql"]
classifiers = [
  "License :: OSI Approved :: MIT License",
  "Programming Language :: Python :: 3 :: Only",
  "Environment :: Console",
  "Topic :: Text Processing :: Markup :: Markdown",
  "Typing :: Typed",
]
dependencies = []          # 零运行时依赖

[project.scripts]
x-article-dl = "x_article_dl.cli:main"

[project.urls]
Homepage = "https://github.com/leoncao80/x-article-dl"
Issues = "https://github.com/leoncao80/x-article-dl/issues"

[tool.hatch.build.targets.wheel]
packages = ["src/x_article_dl"]

[tool.ruff]
line-length = 100
target-version = "py39"
```

`LICENSE`：MIT 全文，首行 `MIT License`，`Copyright (c) 2026 leoncao80`。

`README.md` 必含章节（验收逐条对照）：
1. 是什么/不是什么（仅个人存档与研究；遵守 X ToS；只下自己有权访问的内容）。
2. 原理：三条 GraphQL（UserByScreenName / UserArticlesTweets / TweetResultByRestId）+ Draft.js→MD。
3. 与 gallery-dl 的差异：**保留 header-one/two/three 标题分级**、列表、引用、图片；gallery-dl 的 HTML 转换会丢 header-two。
4. 安装：`pipx install .` / `pip install -e .`；gallery-dl 仅作 Bearer/queryId 来源（可选，可 `--bearer` 替代）。
5. 前置：在 NEC7 上跑；`x_cookies.txt` 从浏览器导出，必须含 `auth_token` 与 `ct0` 且同源同会话。
6. 用法示例 + 退出码表 + 输出结构树。
7. 故障排查表：401/403（ct0 不匹配/会话过期）、429、空列表（features 缺失）、Bearer 读不到、代理、queryId 轮换。
8. 隐私：不收集不上报；`raw.json` 含完整 API 响应，默认不写。

`.gitignore`：

```
__pycache__/
*.py[cod]
.venv/
dist/
build/
*.egg-info/
out/
x_cookies.txt
*.cookies.txt
raw.json
.DS_Store
.ruff_cache/
.pytest_cache/
```

---

## 3. 模块间数据流（CLI 入口 → 落盘）

```
cli.main(argv)
 │
 ├─ build_parser().parse_args()                       # 参数错 → exit 2
 ├─ logging.basicConfig(level=…)
 │
 ├─ 1. 认证
 │    cookies = auth.load_cookies(args.cookies or auth.default_cookie_path())
 │    creds   = auth.Credentials(bearer=auth.read_bearer(args.bearer or None),
 │                              auth_token=cookies["auth_token"], ct0=cookies["ct0"])
 │              ↑ 缺 auth_token/ct0 → ConfigError(exit 2)
 │    client  = auth.XClient(proxy=…, creds=creds, limits=Limits(...))
 │
 ├─ 2. 解析博主
 │    info = listing.resolve_user_id(client, handle)      # --user-id 时跳过
 │           → UserInfo(user_id, screen_name)
 │
 ├─ 3. 翻页（流式生成器）
 │    for ref in listing.iter_articles(client, handle, user_id=info.user_id, limit=…):
 │        url = _endpoints.build_graphql_url(
 │                 "UserArticlesTweets",
 │                 {userId, count:20, includePromotedContent:False, withVoice:True, [cursor]},
 │                 features={articles_preview_enabled: True})
 │        payload = client.graphql(..., session=True)     # 必须带 OAuth2Session 头
 │        ids, next_cursor = listing._parse_entries(payload)
 │        dedupe(tweet_id) → yield ArticleRef(tweet_id, url, idx)
 │        终止：无 cursor / cursor 重复 / 本页新增 0 / max_pages
 │
 ├─ 4. 每篇（串行，默认 1 篇/秒，可用 --no-wait 调整）
 │    article = download.download_article(client, ref.tweet_id, outdir,
 │                    tweet_url=ref.url, force=args.force,
 │                    download_images=not args.no_images)
 │      ├─ fetch_article(client, tweet_id)
 │      │    ├─ client.graphql("TweetResultByRestId",
 │      │    │     {tweetId, withCommunity:False, includePromotedContent:False, withVoice:False},
 │      │    │     features={articles_preview_enabled:True,
 │      │    │               responsive_web_twitter_article_tweet_consumption_enabled:True,
 │      │    │               longform_notetweets_consumption_enabled:True},
 │      │    │     field_toggles={withArticleRichContentState:True,
 │      │    │                    withArticlePlainText:False, …},
 │      │    │     guest=True ← 默认；403/429 退到 session=True，反之亦然)
 │      │    ├─ parse_article_payload(payload)   # → {title,
 │      │    │                   content_state{blocks, entityMap}, media_entities,
 │      │    │                   cover_media, rest_id}
 │      │    └─ converter.convert_article(article_result)
 │      │          ├─ normalize_entities(content_state)     # list → dict[str]
 │      │          ├─ media_map_from(article_result)        # media_id → URL
 │      │          └─ blocks_to_markdown(blocks, entities, media)
 │      │                → (body_md, ordered_media_ids, warnings)
 │      ├─ 图片：去重（URL + media_id）→ client.get_bytes → <bundle>/img/img_NN.ext
 │      │        成功 → body_md 中该远端 URL 换成 img/img_NN.ext
 │      │        失败/过小 → 保留远端 URL + warning（不中断）
 │      └─ write_bundle(article, bundle)     # article.md + page.json（+raw.json），原子写
 │    结果进 results / failures
 │
 ├─ 5. cli._write_index(outdir, handle, results, failures)     # index.md
 └─ 6. 汇总（stderr 人类可读；--json 时 stdout JSON）→ return exit_code
```

**关键时序约束**

- `iter_articles` 是生成器：列表页与下载页交错，第一篇在第一页返回后立即开始下（112 篇不必等 6 页翻完）。
- guest token **按需**获取（首次下载时）；列表阶段只用 session，不浪费一次 `/1.1/guest/activate.json`。
- `index.md` 在所有篇目结束后统一写（避免中途崩溃留下"完成"假象）；增量安全靠每篇 `page.json`。

---

## 4. 坑点清单（现象 / 归属模块 / 移植要求）

> 编号：A=auth，L=listing，D=download，C=converter，CLI=cli，PKG=打包。**共 52 条。**

### A. auth.py（10 条）

| # | 坑 | 现象 / 依据 | 移植要求 |
|---|---|---|---|
| **A1** | Bearer 在 gallery-dl 源码里是**多段相邻字符串拼接**：`"authorization": ("AAAA…" "1Zv7…"),` | 只取第一段 → token 截断，全部请求 401 | 用 `"authorization":\s*\(?((?:\s*"[^"]*")+)` 抓**整组**引号片段再 `"".join`；去 `"Bearer "` 前缀；长度 <50 视为失败。**不要 unquote `%3D`**——X 要的就是百分号编码形态 |
| **A2** | Netscape cookie 文件的 HttpOnly 行以 `#HttpOnly_` 开头 | 旧代码 `if line.startswith("#"): continue` **把 auth_token 整行丢掉**（auth_token 正是 HttpOnly），后续必 401 | 先 `line = line.removeprefix("#HttpOnly_")` 再判注释 |
| **A3** | `UserArticlesTweets` 必须带 `x-twitter-auth-type: OAuth2Session` | 缺该头 → 401（游客态被拒） | `build_headers(session=True)` 固定输出该头，单测覆盖 |
| **A4** | `x-csrf-token` 必须与 `Cookie` 里的 `ct0` 完全一致，且 `auth_token` 与 `ct0` 同属一个会话 | 不一致 → 403 "Could not authenticate you"；从两个浏览器 profile 拼 cookie 必踩 | 只从**同一个** cookie 文件读；`load_cookies` 校验二者都存在；`ct0` 长度 != 160 时 warning |
| **A5** | gallery-dl 路径靠 `glob("~/.local/lib/python*/site-packages/…")[0]` | pipx / venv / 系统包 / 多 Python 版本时找不到；`cands[0]` 顺序不定，可能读到旧版本 | 多候选 + mtime 排序 + `importlib.metadata` 兜底；`--bearer` 逃生（坑 A5） |
| **A6** | 旧 `read_bearer()`/`load_cookies()` 用 `sys.exit()` | 作为库被 import 时直接杀进程，无法被 CLI 统一处理 | 改抛 `ConfigError`，CLI 捕获后 exit 2（坑 A6） |
| **A7** | guest token 请求：`POST https://api.x.com/1.1/guest/activate.json`，`data=b""`，**不能带 cookie** | 带 cookie 或 data 非空 → 403；`guest_token` 有 IP 维度有效期，长跑必须能刷新 | 独立 `guest_token(refresh=True)`；403/429 刷新重试一次；只发 `authorization`+`User-Agent` |
| **A8** | 旧代码 `ssl.create_default_context()` + 硬编码 `ProxyHandler` | 换机器/换代理端口就得改源码；且无法关代理 | proxy 三级解析（`--proxy` / env / 默认）+ `--no-proxy`；`XADL_SSL_NO_VERIFY=1` 才放松校验（默认严格） |
| **A9** | urllib 默认 opener 会吃 `http_proxy/https_proxy/no_proxy` 环境变量 | 在 NEC7 上再叠加 VPN 时出现"请求走错出口"/代理套代理 | 始终自建 opener；`proxy=None` 时用 `ProxyHandler({})` 显式清空 |
| **A10** | `x_tweet_graphql.py` 里硬编码了一枚 Bearer 常量（也进了 git 历史） | token 会轮换，且是泄露面 | 新包**不内置任何 Bearer 字面量**；只从 gallery-dl 现读或 `--bearer` 传入 |

### L. listing.py（12 条）

| # | 坑 | 现象 / 依据 | 移植要求 |
|---|---|---|---|
| **L1** | `UserArticlesTweets` 的 queryId = `G9ehBUadUrsNfz-XnAcV4A` | 写错 → 404 "Query not found" | 常量集中在 `_endpoints.py`，带注释"抓包于 2026-10-19 网页"，**不要**写进变量名字符串拼装 |
| **L2** | 必须带 `x-twitter-auth-type: OAuth2Session`（同 A3） | 401 | 列表全部走 `session=True`，**不要**用 guest token |
| **L3** | `entryId` 形如 `cursor-bottom-1234567890`（带后缀） | 若写成 `eid == "cursor-bottom"` 永远取不到 cursor → 只翻一页 | 用 `eid.startswith("cursor-bottom")`；cursor 值取 `content.value`，**不要**从 entryId 里切 |
| **L4** | 终止条件不足会死循环（X 有时回**同一页**） | 无限翻页、刷爆限流 | 三重终止 + `max_pages` 硬上限：无 cursor / cursor 在 `seen` / 本页新增 0 |
| **L5** | 置顶推文在首页重复出现；跨页也可能重复 | index.md 条目重复、同一篇重下 | 只按 **tweet_id** 去重（dict/`set`），不要按 URL（handle 大小写会变） |
| **L6** | 旧代码用 CLI 输入的 handle 拼 URL | `--handle` 大小写与账号实际不一致 → index 里链接 404 | 用 `UserByScreenName` 返回的 `screen_name` 拼 URL |
| **L7** | 普通 entryId 前缀 `tweet-`，还有 `profile-conversation-`、`cursor-top-`、`who-to-follow-` 等噪声 | 误把非推文 id 当 tweet_id → 下载全失败 | 只收 `tweet-` 前缀，`split("-", 1)[1]` |
| **L8** | 响应结构有两版：`data.user.result.timeline.timeline.instructions`（现网）与 `…result.timeline.instructions`（旧） | 只写一种 → KeyError | `.get("timeline", tl)` 归一；instruction 类型同时支持 `TimelineAddEntries` / `TimelineReplaceEntry` / `TimelinePinEntry` 与 `moduleItems` |
| **L9** | 每篇都调一次 `UserByScreenName` 是额外请求，且部分账号该接口 403 | 列表还没开始就失败 | 提供 `--user-id` 直填；`resolve_user_id` 失败时提示该选项 |
| **L10** | 旧代码在 `while` 循环**里**重建 opener / SSLContext | 每页新建连接池，慢且易触发风控 | opener 在 `XClient.__init__` 建一次，全流程复用 |
| **L11** | `features` 只传了 `{"articles_preview_enabled": True}` | 该组合实测可用，但 X 加新必填 feature 时会返回 `"features cannot be null"` | feature 字典集中在 `_endpoints.FEATURES["UserArticlesTweets"]`，报错信息里回显服务端 message 便于加字段 |
| **L12** | 翻页之间零延迟 | 连续 6 页极快，易被 429 | `page_delay` 默认 0.5s，`--no-wait` 可关 |

### D. download.py（14 条）

| # | 坑 | 现象 / 依据 | 移植要求 |
|---|---|---|---|
| **D1** | `fieldToggles` 必须 `withArticleRichContentState: true` | 为 false（如 `x_tweet_graphql.py`）→ 响应里**没有** `article.content_state`，只能拿到卡片摘要 | 常量写死 true，并加注释说明这是全篇正文的唯一开关；`withArticlePlainText: False` 保持（开了会变纯文本、丢结构） |
| **D2** | `TweetResultByRestId` queryId = `qxWQxcMLiTPcavz9Qy5hwQ` | 写错 404；旧 `x_tweet_graphql.py` 用的 `2ICDjqPd81tulZcYrtpTuQ` 是 **TweetDetail** 的 id，不能混用 | 每个 operation 一个常量，`_endpoints.QUERY_IDS[op]`，禁止跨用 |
| **D3** | `features` 必须含 `responsive_web_twitter_article_tweet_consumption_enabled: true` 与 `longform_notetweets_consumption_enabled: true` | 缺 → GraphQL 400 / article 字段被裁掉 | 默认 feature 集完整给出，可被测试锁定 |
| **D4** | 响应外层可能是 `{"tweet": {...}}`（`TweetWithVisibilityResults` 包装） | 直接取 `result["article"]` → KeyError | `tweet = result.get("tweet", result)`；再按 `__typename` 判 `TweetTombstone` / `TweetUnavailable` → `TweetUnavailableError` |
| **D5** | 非 article 推文（普通推文/转推）没有 `article` 字段；`article` 也可能是 `None` | KeyError 被 `except Exception` 吞掉，日志只有一行 "失败" | 明确抛 `NotAnArticleError`，并在 index 失败清单里标注原因 |
| **D6** | `blocks == []` 或 `entityMap` 缺失 | 产出空 article.md（0 字），看起来"成功" | `ArticleEmptyError`；同时把原始响应写到 `raw.json` 便于排查（`--save-raw` 或失败时自动） |
| **D7** | `media_entities` 的 key 是 `media_id`（可能是数字），JSON 解析后要 `str()` 对齐 | 用 int 查 str 字典 → 全部图片丢失 | 两侧统一 `str(media_id)`；entity 侧 `data.media_id or data.id` 两个字段都试 |
| **D8** | 图片 URL 需要补 size 参数，且原 URL 常带 `?format=…&name=…` | 直接下载可能拿到极小缩略图 | `base = url.split("?")[0] + "?format=jpg&name=large"`；有 `original_img_url` 优先用它 |
| **D9** | 旧代码用 `".png" in u` 判扩展名 | 已被 `?format=jpg` 覆写，判断永远偏 jpg；实际 Content-Type 可能是 png/webp | 按响应 `Content-Type` 定扩展名（jpg/png/webp/gif），兜底 .jpg |
| **D10** | 旧 `article_chars = len(md_body)` | 把 `##`、`[]()` 都算成字，index.md 字数虚高 | `chars = len(plain_text)`（由 converter 顺带返回纯文本） |
| **D11** | 封面图插到 `img_urls[0]` 但正文里没有引用 | 下载了 `img_01.jpg` 却无人引用（孤儿文件），且占掉编号 | 要么在 frontmatter 写 `cover: img/img_00.jpg` 并保留，要么不下载；**默认作为 `cover_image` 字段落盘 + frontmatter 引用** |
| **D12** | `time.strftime("%Y-%m-%d %H:%M")` 直接标"北京时间" | 服务器 TZ 不是 +08 就全错 | 一律 `datetime.now(timezone.utc).isoformat()` 存 UTC；index.md 里显式转 `+08:00` 并标注 |
| **D13** | 无断点续传：重跑覆盖全部、失败篇目下次还得手工挑 | 112 篇跑一半断了要重来 | `page.json` 存在且 `article.md` 非空 → 跳过；`--force` 强制重下 |
| **D14** | 旧代码用 `md_body.replace(url, "img/..")` 做图片替换 | URL 互为前缀时误替换；重复 URL 只保留一份但替换是全局的 | 由 converter 直接产出**占位符/结构化引用**，落盘时再解析成最终路径；至少用 `sorted(by_len_desc)` 或在 URL 两侧加边界 |

### C. converter.py（12 条）

| # | 坑 | 现象 / 依据 | 移植要求 |
|---|---|---|---|
| **C1** | `content_state.entityMap` 是 **list**：`[{"key": "0", "value": {...}}]` | 当 dict 用 → `entities.get(...)` 恒 None，链接/图片全丢 | `normalize_entities()` 转 `{str(key): value}`，兼容 dict |
| **C2** | **`header-one/two/three` 必须映射为 `#`/`##`/`###`** | gallery-dl 走 HTML→MD 会丢 `header-two`；这是本项目相对它的核心优势 | 显式映射表 + 单测（每个 header 类型一条断言），four..six 补 `####..######` |
| **C3** | 列表块渲染时 `continue` 跳过了外层 `i += 1`，依赖内层已经自增 | 移植时改成 `for` 循环或补 incr → 死循环/双倍输出 | 保留"while + 内层自增 + continue"的结构，或改写成 `itertools.groupby` 并加单测 |
| **C4** | `blockquote` 只给首行加 `>` | 多段引用在 MD 里塌成一段 | 逐行 `> ` 前缀；块内空行输出 `>` |
| **C5** | 旧代码完全忽略 `inlineStyleRanges`（BOLD/ITALIC）与 `code-block` | 强调丢失、代码块变普通段落（缩进/换行被吃掉） | 行内样式 → `**`/`*`/`` ` ``；`code-block` → 三反引号围栏（不猜语言） |
| **C6** | 列表 `depth` 被忽略 | 嵌套列表拍平成同级，语义丢失 | `"  " * depth` 缩进；有序列表每层独立计数 |
| **C7** | `atomic` 块无文本，媒体信息只在 `entityRanges` 里 | 处理顺序错 → 图片全丢 | atomic 单独分支：遍历 entity → IMAGE/VIDEO/GIF → 取 media_id → 查 media_map；VIDEO/GIF 用 `![](poster)` 并 warning |
| **C8** | X 的 article `title` 与首个 `header-one` 常重复 | 输出 `# Title` 后再来一个 `# Title` | 正文首块为 header-one 且文本==title 时跳过该块 |
| **C9** | 文本里可能自带 Markdown 元字符（行首 `#`、`1. `、`*`） | 转出来结构错乱 | 行首字符转义（`\#`/`\-`/`1\.`）；块内不转义（避免过度转义） |
| **C10** | 行内 LINK 的 `data.url` 可能是 `t.co` 短链 | 读者点开多一跳，且 t.co 会失效 | 优先 `data.url`，若为 t.co 且 text 长得像 URL 则用 text；可选 `--resolve-tco`（默认关，不额外发请求） |
| **C11** | 旧 `render_text` 用 `sorted(links)` 且区间可能重叠 | 重叠 entity 会重复输出文本 | 按 offset 排序后跳过与前一段重叠的区间；加单测 |
| **C12** | 未知 block 类型（X 以后新增 `divider`/`heading`/`embed`）被静默丢弃 | 内容无声缺失 | 未知类型一律降级为段落 + warning 收集进 `Article.warnings`，写进 page.json |

### CLI. cli.py（2 条）

| # | 坑 | 现象 / 依据 | 移植要求 |
|---|---|---|---|
| **CLI1** | 旧脚本 `--limit` 在列表全量抓完后才切片 | 限制 2 篇仍要翻 6 页（浪费时间 + 限流风险） | `islice` 作用在生成器上，取够即停 |
| **CLI2** | 旧 `fetch_user_articles.py` 恒退出 0，部分失败无信号 | CI/脚本无法判断成败 | 语义化退出码 0/1/2/3；`--json` 输出 `{ok, failed, exit_code}` |

### PKG. 打包（2 条）

| # | 坑 | 现象 / 依据 | 移植要求 |
|---|---|---|---|
| **PKG1** | `sys.path.insert(0, dirname(__file__))` + 兄弟模块 import | 装成包后全部失效，且易与同名模块冲突 | `src/` 布局 + 包内相对导入，`[tool.hatch.build.targets.wheel] packages` 指定 |
| **PKG2** | `.gitignore` 若漏掉 `x_cookies.txt` | 含 `auth_token` 的登录态被提交到公开仓库 | 显式忽略 `x_cookies.txt` / `*.cookies.txt` / `out/` / `raw.json` |

---

## 5. 明确可丢弃的过时逻辑 + 替代方案

| 旧代码 | 位置 | 为什么扔 | 新包替代 |
|---|---|---|---|
| Chrome/CDP 抓取全链路：`find_chrome` / `--user-data-dir=~/.chrome-x-fetcher` / 远程调试端口 9333 / Singleton 锁清理 / `Page.navigate` / `document.body.innerText` | `x_browser_fetch.py`（404 行，全文件） | 需要本机 Chrome + 已登录 profile，登录态失效就得重拷 profile；且靠 `N min read`、`@handle`、页脚文案（"Follow me @"、"Join … followers"）等**易变文案启发式**切正文，X 改文案即崩 | `download.fetch_article()` 直取 `content_state.blocks`，结构稳定、无 DOM 依赖 |
| `extract_article(body_text)` 的标题/正文猜测与页脚截断表 | `x_browser_fetch.py:165` | 上述启发式；还会把正文里恰好出现 "More Articles" 的段落截掉 | 无需替代（blocks 自带结构） |
| `tier_browser()` + `--browser` 参数 | `fetch_x.py:94` | 同上 | `x-article-dl <handle>` |
| yt-dlp 档（`yt-dlp --dump-single-json` 取 `description`） | `fetch_x.py:146` | 只能拿 description 摘要，遇到 summary 卡片会跟随跳转 404；还需 NEC7 SSH | `listing` + `download` |
| oembed 档（`publish.twitter.com/oembed`） | `fetch_x.py:206` | 明确截断长文，对 Article 无用 | 删除 |
| t.co 短链解析（`curl %{redirect_url}`，注释里还写着"urllib 回 404"） | `fetch_x.py:225` | 只为"猜"文章地址，现在直接有 tweet_id | 删除 |
| SSH 到 NEC7 / `ON_NEC7 = "nec7" in hostname` 分档 | `fetch_x.py:46,70` | 网络拓扑写死在代码里 | 只保留 `--proxy` 参数，README 说明在 NEC7 上跑 |
| `x_tweet_graphql.py` 硬编码 BEARER + `TweetDetail` QID + `withArticleRichContentState: False` + `urlencode(separators=(",",":"))` | 整文件 | Bearer 过期/泄露；queryId 属于另一个 operation；false 导致拿不到正文；紧凑序列化与现网 `quote(json.dumps(...))` 不一致（X 两种都收，但没必要留两套） | `auth.read_bearer()` + `_endpoints.build_graphql_url()` 统一一种序列化 |
| `list_article_links.py` 的 `sys.path.insert` + `from fetch_article_api import ...` 跨脚本 import | 两处 | 非包结构，装不上 PyPI | 包内相对导入 |
| `resolve_user_id()` 里临时 `glob` 找 `UserByScreenName` QID + 魔数兜底 `ck5KkZ8t5cOmoLssopN99Q` | `list_article_links.py:107` | 魔数无来源、无版本记录；`cands[0]` 未做存在性判断（gallery-dl 未装直接 IndexError） | `auth.read_query_id()` + `_endpoints.QUERY_IDS` 显式常量 + `ConfigError` |
| 每页新建 `build_opener` / 每图新建 opener | `list_article_links.py:83`、`fetch_article_api.py:download()` | 连接不复用 | `XClient` 单例 opener |
| `fetch_one()` 里"抓 + 转 + 下图 + 写 md + 写 json + 返回摘要"六合一 | `fetch_article_api.py:190` | 无法单测、无法只抓不写 | `fetch_article` / `convert_article` / `_download_image` / `write_bundle` 四层拆分 |
| 封面图强行 `img_urls.insert(0, …)` 但不进正文 | `fetch_article_api.py:216` | 孤儿文件 + 编号错位 | `ImageAsset(is_cover=True)` + frontmatter `cover:` |
| `fetch_user_articles.py` 的编排与 index 拼装 | 全文件 | 与 CLI 重复；`os.makedirs` 在失败前就建目录留下空壳 | `cli._run()` + 失败后才按需建 bundle；空目录最后清理 |
| README 里"必须用浏览器读 Article / 单独 article URL 404 / gallery-dl 不可用"等结论 | 各 README | 2026-10-19 已被纯 API 方案推翻 | 新 README 重写（见 2.6） |
| `out/` 里散落的 `fetch_x` 产物（`out/<id>.md` 平铺） | 目录产物 | 无图片目录、无元数据、无索引 | `<outdir>/<tweet_id>/{article.md,img/,page.json}` + index.md |

**保留但需要改造的**：`PROXY = "http://127.0.0.1:7897"`（→ 可配置，坑 A8）、`UA` 常量（→ `--ua` 可覆盖）、
`count: 20` / `withVoice: True` / `includePromotedContent: False` 的 variables 组合（实测可用，原样保留）、
`time.sleep(1)` 的篇间节流（→ `--delay`，默认 1.0）。

---

## 6. 编码顺序建议（给实现者的路线图）

1. `errors.py` + `_endpoints.py`（纯常量/异常，零风险）
2. `converter.py` + 用 `raw.json` 样本做单测（header/list/quote/atomic/link/nested/未知块，**C2 必须有断言**）
3. `auth.py`（`read_bearer` 用真实 gallery-dl 源码跑通，`load_cookies` 用含 `#HttpOnly_` 的样本文件）
4. `listing.py`（对着一次真实响应调 `_parse_entries`，确认 cursor 取值）
5. `download.py`（先 `fetch_article` 打印 title/chars，再接入图片/落盘）
6. `cli.py` + 退出码 + index.md
7. pyproject / LICENSE / README / .gitignore
8. 端到端验收：`x-article-dl eng_khairallah1 --limit 3` → 检查 3 个 bundle、index.md、图片本地化、
   `article.md` 里 `##` 级标题存在（对比 gallery-dl 输出确认 header-two 未丢）

## 7. 设计自检（对照任务验收项）

- [x] 每个模块的函数/类签名（参数、返回值、异常）
- [x] 模块间数据流：CLI → auth → listing → download → converter → 落盘 → index
- [x] 坑点清单 52 条，逐条标注归属模块与移植要求（含任务点名的 6 条必查项：A1、A3、L3+L4、C2、A8、D1+D2+D3）
- [x] 过时逻辑与替代方案（第 5 节，14 项）
- [x] 交付物清单：pyproject（含 console entry）、MIT LICENSE（leoncao80）、README、.gitignore
- [x] 未修改任何源码，未编写业务代码
