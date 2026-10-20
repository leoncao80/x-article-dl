"""The redaction tool must keep the payload replayable while dropping the content."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

spec = importlib.util.spec_from_file_location("redact_raw", ROOT / "tools" / "redact_raw.py")
redact_raw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(redact_raw)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "raw_article.json"


def blocked(payload):
    return payload["data"]["tweetResult"]["result"]["article"]["article_results"]["result"][
        "content_state"
    ]["blocks"]


class RedactTests(unittest.TestCase):
    def setUp(self):
        self.original = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.cleaned = redact_raw.redact(self.original)

    def test_author_is_anonymised(self):
        user = self.cleaned["data"]["tweetResult"]["result"]["core"]["user_results"]["result"]
        self.assertEqual(user["core"]["screen_name"], "example_writer")
        self.assertEqual(user["legacy"]["name"], "Example Writer")

    def test_block_shape_and_lengths_survive(self):
        before, after = blocked(self.original), blocked(self.cleaned)
        self.assertEqual(len(before), len(after))
        for old, new in zip(before, after):
            self.assertEqual(old["type"], new["type"])
            self.assertEqual(len(old["text"]), len(new["text"]))
            self.assertEqual(old["entityRanges"], new["entityRanges"])

    def test_text_is_replaced_but_structure_is_not(self):
        before, after = blocked(self.original), blocked(self.cleaned)
        self.assertNotEqual(before[1]["text"], after[1]["text"])
        self.assertEqual(after[1]["inlineStyleRanges"], before[1]["inlineStyleRanges"])
        self.assertEqual(list(after[1]["text"])[0], "x")

    def test_media_hosts_are_neutralised(self):
        info = self.cleaned["data"]["tweetResult"]["result"]["article"]["article_results"][
            "result"
        ]["media_entities"][0]["media_info"]
        self.assertTrue(info["original_img_url"].startswith("https://example.invalid/"))
        self.assertTrue(info["original_img_url"].endswith(".jpg"))
        # media ids stay: media_map keys depend on them
        self.assertEqual(
            self.cleaned["data"]["tweetResult"]["result"]["article"]["article_results"][
                "result"
            ]["media_entities"][0]["media_id"],
            "700000000000000003",
        )

    def test_keep_text_is_opt_in(self):
        kept = redact_raw.redact(self.original, keep_text=True)
        before = blocked(self.original)[1]["text"]
        self.assertEqual(blocked(kept)[1]["text"], before)

    def test_text_with_a_url_is_still_replaced(self):
        payload = {"legacy": {"full_text": "read https://example.com/secret-article now"}}
        cleaned = redact_raw.redact(payload)
        self.assertNotIn("secret-article now", cleaned["legacy"]["full_text"])
        self.assertEqual(len(cleaned["legacy"]["full_text"]), len(payload["legacy"]["full_text"]))

    def test_bare_hostname_field_is_replaced(self):
        payload = {"display_url": "someone.example"}
        cleaned = redact_raw.redact(payload)
        self.assertEqual(cleaned["display_url"], "example.invalid")

    def test_preview_text_is_replaced(self):
        payload = {"preview_text": "a preview of the article"}
        cleaned = redact_raw.redact(payload)
        self.assertNotIn("preview", cleaned["preview_text"])

    def test_markdown_keeps_fences_but_loses_words(self):
        source = "```python\nimport secret_module\n```"
        cleaned = redact_raw.redact({"markdown": source})
        self.assertEqual(cleaned["markdown"].count("```"), 2)
        self.assertNotIn("secret", cleaned["markdown"])
        self.assertEqual(len(cleaned["markdown"]), len(source))


if __name__ == "__main__":
    unittest.main()
