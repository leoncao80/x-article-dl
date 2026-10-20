# Architecture

A short map of the codebase for contributors. The full Chinese design draft
(written before implementation) and the handoff notes are kept as
[`xadl-design.zh-CN.md`](xadl-design.zh-CN.md) and
[`handoff.zh-CN.md`](handoff.zh-CN.md).

## Data flow

```
cli.main()
 │  1. auth     cookies (auth_token + ct0) → Credentials
 │               bearer read live from gallery-dl's source
 │               one opener per run, explicit proxy handling
 │
 │  2. resolve  UserByScreenName → userId + canonical screen_name
 │
 │  3. list     iter_articles generator (streaming, not list-then-download)
 │               stops on: no cursor / cursor loop / zero new ids / max_pages
 │
 │  4. article  TweetResultByRestId (guest first, session fallback)
 │               parse payload → content_state → converter → Markdown
 │               download images, rewrite URLs to local paths
 │
 │  5. write    <tweet_id>/article.md + page.json (atomic replace)
 │
 │  6. index    index.md (summary + failure list) → semantic exit code
```

## Modules

| Module | Responsibility | Hard rule |
|---|---|---|
| `auth.py` | bearer, cookies, proxy, retries, guest token | library code never calls `sys.exit()` or `print()` |
| `listing.py` | user lookup, article timeline paging | dedupe by `tweet_id` only; URLs use the API's `screen_name` |
| `download.py` | payload unwrapping, image download, bundle writing | a failing image degrades to a warning |
| `converter.py` | Draft.js blocks → Markdown (pure, no IO) | heading levels never degrade; each block's fragment ends with `\n` |
| `cli.py` | argparse, orchestration, `index.md`, exit codes | exceptions map to 0/1/2/3 |
| `_endpoints.py` | query IDs, features, field toggles | query IDs appear only here |
| `errors.py` | exception hierarchy with `exit_code` | failures are typed, not stringly |

## Invariants worth preserving

1. **Heading mapping** — `header-one/two/three` → `#`/`##`/`###`. This is the main
   advantage over HTML-based tools; a regression here is a project-level bug.
2. **Output shape** — every block produces a fragment ending in `\n`, joined with
   `"\n".join(parts).strip() + "\n"`. Changing this join rule breaks paragraph and
   list detection.
3. **One emission per character** — inline ranges are segmented, so overlapping
   link/style ranges can never duplicate text.
4. **Atomic writes** — `.tmp` + `os.replace`, so an interrupt never leaves a
   half-written article.
5. **Resume** — `page.json` plus a non-empty `article.md` means "skip" unless
   `--force`.
6. **Explicit proxy** — `ProxyHandler({})` when no proxy is configured, so
   environment variables cannot silently reroute traffic.

## Real-world findings that shaped the code

These came from inspecting actual payloads, and are the reason the design draft
was adjusted during implementation:

- `entityMap` is a **list** of `{key, value}` in the article payload (a dict in
  older shapes); both are handled.
- Inline styles are capitalised: `"Bold"`, `"Italic"`.
- Code blocks arrive as `atomic` blocks with a **`MARKDOWN`** entity whose
  `data.markdown` is already a fenced block. Insert it verbatim.
- Embedded tweets arrive as `atomic` **`TWEET`** entities with `data.tweetId`.
- `media_entities` is often empty; covers live in `cover_media`, and body images
  may appear as `IMAGE` atoms.
- In a 12-article sample: 80 `MARKDOWN`, 3 `TWEET`, 1 `LINK`, 0 `IMAGE` atoms —
  implementing only the image branch would have dropped every code block.
- gallery-dl writes bearer tokens as concatenated adjacent string literals; the
  whole group must be joined.
- Netscape cookie files mark HttpOnly rows with a `#HttpOnly_` prefix, and
  `auth_token` is one of them.

## Testing strategy

| Layer | What it catches | Cost |
|---|---|---|
| `tests/test_converter.py` | blocks → Markdown semantics, heading levels, lists, atoms | milliseconds |
| `tests/test_payload.py` | payload unwrapping, tombstones, timestamps | milliseconds |
| `tests/test_download_flow.py` | bundle layout, image rewrite, resume, warnings (fake client) | ~0.5 s |
| `tests/test_auth.py` | proxy precedence, Netscape parsing, domain filtering | milliseconds |
| `tests/test_payload_fixture.py` | replay of a hand-written payload: wrapper, author, blocks, entities, media | milliseconds |
| `tests/test_payload_live.py` | replay of a recorded + redacted payload: upstream block mix, headings, code fences | milliseconds |
| `tests/test_redact_raw.py` | the redaction tool keeps the payload replayable and the content gone | milliseconds |
| `smoke` workflow | live API / query ID / feature drift | needs cookies |

Two payload fixtures guard this layer without cookies:
`tests/fixtures/raw_article.json` is hand-written and covers every block and entity
type on purpose; `tests/fixtures/raw_article_live.json` is a recording of a real
response (163 blocks, 20 `header-two`, three `MARKDOWN` atoms) from 2026-10-21,
passed through `tools/redact_raw.py`. Re-record the live one after an upstream
schema change: `--save-raw`, then
`python3 tools/redact_raw.py raw.json > tests/fixtures/raw_article_live.json`.
See `tests/fixtures/README.md` for what redaction removes and what it keeps.
