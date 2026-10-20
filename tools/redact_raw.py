#!/usr/bin/env python3
"""Turn a recorded ``raw.json`` into a fixture that is safe to commit.

``x-article-dl download --save-raw`` writes the untouched GraphQL response, which
contains the author, the article text and signed media URLs. Committing that to a
public repository would leak whatever the account read. This script keeps the
*shape* the parser cares about and removes everything else:

- ``screen_name`` / ``name``          -> ``example_writer`` / ``Example Writer``
- every URL                          -> ``https://<host>.invalid/...``, same path
- every id-like scalar (rest_id, tweetId, media_id, mediaId, cursor, ...) is kept
  as-is: they are opaque numbers, not personal data, and offsets/media maps rely on
  them staying consistent
- block text                         -> length-preserving filler, so Draft.js
  offsets, entityRanges and inlineStyleRanges stay valid

Usage:

    python3 tools/redact_raw.py raw.json > tests/fixtures/raw_article.json
    python3 tools/redact_raw.py --keep-text raw.json > out/fixture.json

``--keep-text`` keeps the wording (useful while debugging a converter bug) and is
**not** safe to publish.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from typing import Any

HANDLE_FIELDS = {"screen_name", "screenName", "username"}
NAME_FIELDS = {"name", "display_name"}
TEXT_FIELDS = {"text", "title", "full_text", "description", "caption", "markdown"}
URL_RE = re.compile(r"https?://[^\s\"'<>]+")
# hostnames that are infrastructure rather than personal: keep them recognisable
KEEP_HOSTS = {"pbs.twimg.com", "video.twimg.com", "abs.twimg.com", "t.co"}

def _filler(text: str) -> str:
    """Same length, same shape of whitespace, no original wording."""
    out = []
    for char in text:
        if char.isspace():
            out.append(char)
        elif char.isdigit():
            out.append("0")
        else:
            out.append("x")
    return "".join(out)


def _url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return "https://example.invalid/"
    host = parsed.hostname or "example"
    if host in KEEP_HOSTS:
        redacted_host = host
    else:
        redacted_host = "example.invalid"
    path = parsed.path or "/"
    # keep the file extension so media sniffing still has something to chew on
    return urllib.parse.urlunsplit((parsed.scheme or "https", redacted_host, path, "", ""))


def redact(node: Any, *, key: str = "", keep_text: bool = False) -> Any:
    if isinstance(node, dict):
        out = {}
        for child_key, value in node.items():
            out[child_key] = redact(value, key=child_key, keep_text=keep_text)
        return out
    if isinstance(node, list):
        return [redact(item, key=key, keep_text=keep_text) for item in node]
    if isinstance(node, str):
        if key in HANDLE_FIELDS:
            return "example_writer"
        if key in NAME_FIELDS:
            return "Example Writer"
        if URL_RE.search(node):
            return URL_RE.sub(lambda match: _url(match.group(0)), node)
        if key in TEXT_FIELDS and not keep_text:
            return _filler(node)
        return node
    return node


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Anonymise a recorded raw.json payload")
    parser.add_argument("path", help="path to raw.json written by --save-raw")
    parser.add_argument(
        "--keep-text",
        action="store_true",
        help="keep article wording (NOT safe to publish)",
    )
    parser.add_argument("--indent", type=int, default=2)
    args = parser.parse_args(argv)

    try:
        with open(args.path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as exc:
        print("cannot read %s: %s" % (args.path, exc), file=sys.stderr)
        return 1
    except ValueError as exc:
        print("%s is not valid JSON: %s" % (args.path, exc), file=sys.stderr)
        return 1

    cleaned = redact(payload, keep_text=args.keep_text)
    json.dump(cleaned, sys.stdout, ensure_ascii=False, indent=args.indent)
    sys.stdout.write("\n")
    if not args.keep_text:
        print(
            "redacted; check the diff before committing (fixtures are public)",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
