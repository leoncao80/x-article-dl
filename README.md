# x-article-dl

[![ci](https://github.com/leoncao80/x-article-dl/actions/workflows/ci.yml/badge.svg)](https://github.com/leoncao80/x-article-dl/actions/workflows/ci.yml)
[![smoke](https://github.com/leoncao80/x-article-dl/actions/workflows/smoke.yml/badge.svg)](https://github.com/leoncao80/x-article-dl/actions/workflows/smoke.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Download every X (Twitter) Article of a user as clean, structured Markdown.

No browser, no Selenium, no headless Chrome. Zero runtime dependencies — just the
Python standard library and three private GraphQL calls.

> 中文说明见 [README.zh-CN.md](README.zh-CN.md)。

Verified end to end on `@eng_khairallah1`: 112 articles downloaded in full.
The same API listing finds 112 articles where scrolling the web UI found 56.

## What it is / isn't

**It is** a small CLI for personal archiving and research: give it a handle, get
every Article as Markdown, with images downloaded locally and an index file.

**It isn't** a scraping framework. It does not touch regular tweets or the home
timeline, it does not run as a service, and it does not bypass paywalls or access
controls.

Using it means you agree to the [X Terms of Service](https://x.com/tos): only
download content you are allowed to access, keep request rates low, and do not
republish other people's work. The authors are not responsible for how you use it.

## How it works

Three GraphQL operations. Query IDs are captured from the web client and kept in
one table (`src/x_article_dl/_endpoints.py`) so a rotation is a one-line change.

| Purpose | Operation | Critical detail |
|---|---|---|
| handle → userId | `UserByScreenName` | Use the API's canonical `screen_name` for URLs |
| list all articles | `UserArticlesTweets` | Requires `x-twitter-auth-type: OAuth2Session`; cursor paging |
| fetch one article | `TweetResultByRestId` | `fieldToggles.withArticleRichContentState=true` is the only way to get the full body |

Article bodies are Draft.js `content_state.blocks` (headings, lists, quotes,
atomic media). `converter.py` turns them into Markdown directly — no HTML step.

Authentication: the bearer token is read live from gallery-dl's source (never
bundled), and session cookies come from a Netscape-format export of your browser.

## Why not gallery-dl?

gallery-dl converts HTML to Markdown, which drops `header-two` and flattens lists
and quotes. This project reads the editor's structured blocks instead, so:

- `header-one/two/three(/four…six)` → `#`/`##`/`###`/`####…` (no lost levels)
- ordered/unordered lists keep their grouping, per-depth numbering and nesting
- `blockquote` is prefixed line by line
- inline bold/italic, `LINK` entities, `IMAGE` atoms
- X's real `MARKDOWN` (fenced code) and `TWEET` (embedded tweet) atoms
- unknown block types degrade to a paragraph plus a warning — nothing is dropped silently

## Install

```bash
pipx install git+https://github.com/leoncao80/x-article-dl.git
# or, for development
git clone https://github.com/leoncao80/x-article-dl.git
cd x-article-dl && pip install -e .
```

Requires Python 3.9+. No third-party runtime dependencies.

`gallery-dl` is optional and only used as a source for the bearer token and query
IDs. Without it, pass `--bearer TOKEN`.

## Requirements

### Network

- Access to `api.x.com`, `x.com` and `pbs.twimg.com`.
- **Direct connection by default.** If your network needs a proxy, configure one:

  ```bash
  x-article-dl <handle> --proxy http://127.0.0.1:7897   # this run only
  export XADL_PROXY=http://127.0.0.1:7897               # this shell
  # PowerShell: $env:XADL_PROXY="http://127.0.0.1:7897"
  ```

  Precedence: `--proxy` > `XADL_PROXY` > `HTTPS_PROXY`/`https_proxy` > direct.
  Use `--no-proxy` to force a direct connection.

### Cookies (required)

The listing endpoint only accepts an authenticated session. You need a
**Netscape-format** cookie file that:

- contains `auth_token` and `ct0`;
- was exported from the **same browser session** (mixing profiles always yields 403);
- keeps the `#HttpOnly_` prefix — `auth_token` is HttpOnly.

Export options:

| Option | How |
|---|---|
| Browser extension | Install a Netscape cookie exporter (e.g. "Get cookies.txt LOCALLY") and export on the **x.com** page |
| Manually | DevTools → Application → Cookies → `https://x.com`, then write `auth_token` and `ct0` as two lines |

Line format (TAB-separated, `#HttpOnly_` prefix required):

```
#HttpOnly_.x.com	TRUE	/	TRUE	0	auth_token	<value>
.x.com	TRUE	/	TRUE	0	ct0	<value>
```

Point the tool at the file:

```bash
export XADL_COOKIES=~/x_cookies.txt        # Linux / macOS
# PowerShell: $env:XADL_COOKIES="C:\Users\you\x_cookies.txt"

x-article-dl <handle> --cookies /path/to/x_cookies.txt
```

Auto-detection order: `--cookies` → `XADL_COOKIES` → `./x_cookies.txt` →
`~/.config/x-article-dl/cookies.txt`.

> Treat cookies like a password: `.gitignore` excludes `x_cookies.txt` and
> `*.cookies.txt`. Never commit them or paste them into issues or public CI logs.

## Usage

```bash
x-article-dl eng_khairallah1
x-article-dl @eng_khairallah1 --limit 2 --outdir /tmp/xadl-verify
x-article-dl eng_khairallah1 --no-images --save-raw -v
```

| Option | Default | Description |
|---|---|---|
| `handle` | required | May be prefixed with `@` |
| `--outdir DIR` | `./out/<handle>_articles` | Output directory |
| `--limit N` | 0 (all) | Download only the newest N articles; stops paging early |
| `--proxy URL` / `--no-proxy` | direct | Precedence: `--proxy` > `XADL_PROXY` > `HTTPS_PROXY` > direct |
| `--cookies FILE` | auto-detected | Netscape cookie file |
| `--bearer TOKEN` | read from gallery-dl | Escape hatch (listing still needs cookies) |
| `--user-id UID` | resolved | Skip `UserByScreenName` and save a request |
| `--force` | off | Ignore existing `page.json` and re-download |
| `--no-images` | off | Keep remote image URLs instead of downloading |
| `--save-raw` | off | Save the full API response as `raw.json` (sensitive) |
| `--json` | off | Machine-readable summary on stdout (logs stay on stderr) |
| `--delay SEC` | 1.0 | Delay between articles; `--no-wait` disables all waiting |
| `-q` / `-v` | INFO | `-q` warnings only; `-v` debug details |

**Exit codes**

| Code | Meaning |
|---|---|
| `0` | All articles succeeded |
| `1` | Some articles failed |
| `2` | Configuration or authentication error |
| `3` | Everything failed (empty listing or all articles failed) |
| `130` | Interrupted |

**Output layout**

```
<outdir>/
├── index.md                 # summary table + failure list
└── <tweet_id>/
    ├── article.md           # YAML frontmatter + "# Title" + body (local image paths)
    ├── page.json            # metadata, also used for resume
    ├── raw.json             # only with --save-raw
    └── img/img_00.jpg ...   # cover is img_00, body images start at img_01
```

`chars` counts plain text only — Markdown syntax such as `##` or `[]()` is excluded.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `HTTP 401` | Missing `x-twitter-auth-type` header or no `auth_token` in cookies |
| `HTTP 403` "Could not authenticate you" | `ct0` and cookie are from different sessions, or the session expired; re-export from the same browser |
| `HTTP 429` | Rate limited. By default the tool waits for `x-rate-limit-reset`; use `--no-wait` or lower the request rate |
| Empty listing | Missing `features`, or the account has no Articles; run with `-v` to see the server message |
| `Bearer token not found` | gallery-dl is not installed or changed its layout; pass `--bearer` |
| `Query not found` / 404 | A query ID rotated; update `_endpoints.QUERY_IDS` (one place). `-v` shows the actual URL |
| Cannot reach `api.x.com` | Direct connection by default; configure `--proxy`/`XADL_PROXY` if your network requires it |
| Article OK but image failed | Single-image failures only add a warning and keep the remote URL; details are in `page.json` |
| `content_state.blocks is empty` | Not a long-form Article, or `withArticleRichContentState` did not apply |

## Privacy

- No telemetry, no analytics, no data collection.
- Cookies and bearer tokens stay in memory and request headers; logs never print
  them (`__repr__` masks secrets).
- `raw.json` contains the complete API response (including account metadata) and
  is only written with `--save-raw`.

## Development

```bash
python -m unittest discover -s tests -v   # 42 offline unit tests
ruff check src tests
```

Design notes and the pitfall catalogue live in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Chinese project archives (design draft, handoff notes) are kept as
`docs/*.zh-CN.md`.

## License

MIT © 2026 leoncao80. See [LICENSE](LICENSE).
