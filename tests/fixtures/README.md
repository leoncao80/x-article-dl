# Fixtures

`raw_article.json` holds one Article tweet in the shape X returns it (wrapper,
author, Draft.js `content_state`, entities, media), with no real data in it. It is
replayed by `tests/test_payload_fixture.py`, so a change in what X returns — a new
block type, a moved field, a renamed wrapper — fails in CI without cookies.

The file is **hand-written, not a recording**. Replace it with a real one as soon
as cookies are at hand: `--save-raw` plus the command below. Until then it guards the
parser and converter against our own regressions, not against upstream drift.

It carries no real account, article text or media URL:

- handles and display names are `example_writer` / `Example Writer`
- every block `text`/`title` is a length-preserving filler, so Draft.js offsets,
  `entityRanges` and `inlineStyleRanges` stay meaningful
- every URL points at `example.invalid`, except `pbs.twimg.com`-style hosts, which
  keep their host so media handling stays realistic, and the path/extension are
  preserved
- ids (`rest_id`, `tweetId`, `media_id`) are unchanged: they are opaque numbers and
  `media_map` keys depend on them

## Recording a fresh fixture

```bash
# needs X cookies; --save-raw writes the untouched response next to the bundle
xadl download <tweet-url> --cookies x_cookies.txt --save-raw -o out/
python3 tools/redact_raw.py out/<bundle>/raw.json > tests/fixtures/raw_article.json
```

Check `git diff` before committing: whatever the account could read is in that
file, and this repository is public. While debugging a converter bug you can pass
`--keep-text` to keep the wording, but never commit that output.
