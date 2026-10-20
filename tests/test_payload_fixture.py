"""Offline replay of a recorded GraphQL payload (closes the gap in ARCHITECTURE.md).

`fixtures/raw_article.json` is an anonymised payload with the same shape X returns
for an Article tweet: wrapper, author, Draft.js `content_state`, entities, media.
Replaying it means a converter or parser regression fails in CI without cookies.

Re-record it after an upstream schema change:

    python3 tools/redact_raw.py --text-mode keep path/to/raw.json \
        > tests/fixtures/raw_article.json

See `tests/fixtures/README.md`.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from x_article_dl.converter import convert_article  # noqa: E402
from x_article_dl.download import parse_article_payload  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "raw_article.json"


class RawPayloadReplayTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.article = parse_article_payload(self.payload)

    def test_unwraps_metadata(self):
        self.assertEqual(self.article["title"], "A Redacted Article For Replay Tests")
        self.assertEqual(self.article["author_handle"], "example_writer")
        self.assertEqual(self.article["author_name"], "Example Writer")
        self.assertEqual(self.article["article_id"], "900000000000000001")
        self.assertEqual(self.article["tweet_id"], "1234567890123456789")
        self.assertEqual(self.article["created_at"], "2026-10-19T01:30:00Z")

    def test_every_block_is_understood(self):
        # A new upstream block type would land here as a warning, not a silent drop.
        _body, _media, warnings = convert_article(self.article["article_result"])
        self.assertEqual(warnings, [])

    def test_markdown_keeps_the_structure(self):
        body, media, _warnings = convert_article(self.article["article_result"])
        # header-one repeating the title is dropped (pitfall C8)
        self.assertNotIn("# A Redacted Article For Replay Tests", body)
        self.assertIn("## 为什么这个字段会变", body)
        self.assertIn("**Opening** paragraph", body)
        self.assertIn("(https://example.invalid/docs)", body)
        self.assertIn("- unordered one", body)
        self.assertIn("1. ordered one", body)
        self.assertIn("> A quoted sentence.", body)
        self.assertIn("```python", body)
        self.assertIn("from a MARKDOWN atom", body)
        self.assertIn("1200000000000000002", body)

    def test_media_entity_is_resolved_to_a_url(self):
        _body, media, _warnings = convert_article(self.article["article_result"])
        self.assertIn(
            ("700000000000000003", "https://pbs.example.invalid/media/700000000000000003.jpg"),
            media,
        )


if __name__ == "__main__":
    unittest.main()
