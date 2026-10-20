"""Replay a *real* recorded payload after redaction.

`fixtures/raw_article_live.json` is a recording of one Article tweet, passed
through ``tools/redact_raw.py``. Unlike the hand-written fixture it carries the
exact shape X returned on 2026-10-21 (163 blocks, 20 header-two blocks, three
``MARKDOWN`` atoms), so a change in the upstream schema fails here first.

The recording itself is not committed; only the redacted copy is. Text is
length-preserving filler, handles and URLs are neutralised, and ids are kept
because media maps and offsets depend on them.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from x_article_dl.converter import convert_article  # noqa: E402
from x_article_dl.download import parse_article_payload  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "raw_article_live.json"


class LivePayloadReplayTests(unittest.TestCase):
    def setUp(self):
        self.raw_text = FIXTURE.read_text(encoding="utf-8")
        self.payload = json.loads(self.raw_text)
        self.article = parse_article_payload(self.payload)

    def test_no_real_identifiers_survive(self):
        lowered = self.raw_text.lower()
        for needle in ("eng_khairallah1", "khairallah", "grok bot", "xai"):
            self.assertNotIn(needle, lowered)

    def test_metadata_is_anonymised_but_typed(self):
        self.assertEqual(self.article["author_handle"], "example_writer")
        self.assertEqual(self.article["author_name"], "Example Writer")
        self.assertTrue(self.article["title"])
        self.assertTrue(self.article["tweet_id"].isdigit())
        self.assertTrue(self.article["created_at"].endswith("Z"))

    def test_block_mix_matches_the_recording(self):
        kinds = [block["type"] for block in self.article["blocks"]]
        self.assertEqual(len(kinds), 163)
        self.assertEqual(kinds.count("header-two"), 20)
        self.assertEqual(kinds.count("atomic"), 3)
        self.assertIn("blockquote", kinds)
        self.assertIn("ordered-list-item", kinds)

    def test_every_atomic_entity_is_a_markdown_atom(self):
        kinds = [entity["value"]["type"] for entity in self.payload["data"]["tweetResult"]["result"]
                 ["article"]["article_results"]["result"]["content_state"]["entityMap"]]
        self.assertEqual(kinds.count("MARKDOWN"), 3)

    def test_conversion_keeps_headings_and_code_fences(self):
        body, media, warnings = convert_article(self.article["article_result"])
        self.assertEqual(warnings, [])
        self.assertEqual(body.count("\n## "), 20)
        self.assertEqual(body.count("```"), 6)  # three fenced blocks
        self.assertEqual(media, [])  # no inline media in this recording
        self.assertGreater(len(body), 20000)


if __name__ == "__main__":
    unittest.main()
