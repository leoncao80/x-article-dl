"""End-to-end bundle tests with a fake HTTP client (no network)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from x_article_dl import download  # noqa: E402

IMAGE_URL = "https://pbs.twimg.com/media/abc.jpg"

ARTICLE_RESULT = {
    "rest_id": "555",
    "title": "With Image",
    "content_state": {
        "blocks": [
            {
                "key": "p1",
                "text": "body text",
                "type": "unstyled",
                "data": {},
                "inlineStyleRanges": [],
                "entityRanges": [],
            },
            {
                "key": "img",
                "text": " ",
                "type": "atomic",
                "data": {},
                "inlineStyleRanges": [],
                "entityRanges": [{"offset": 0, "length": 1, "key": 0}],
            },
        ],
        "entityMap": [{"key": "0", "value": {"type": "IMAGE", "data": {"media_id": "777"}}}],
    },
    "media_entities": [
        {"media_id": "777", "media_info": {"original_img_url": IMAGE_URL}}
    ],
    "cover_media": {},
}

PAYLOAD = {
    "data": {
        "tweetResult": {
            "result": {
                "__typename": "Tweet",
                "rest_id": "123",
                "legacy": {"created_at": "Mon Oct 19 09:35:56 +0000 2026"},
                "core": {
                    "user_results": {
                        "result": {"core": {"screen_name": "author", "name": "Author"}}
                    }
                },
                "article": {"article_results": {"result": ARTICLE_RESULT}},
            }
        }
    }
}


class FakeClient:
    def __init__(self, payload=PAYLOAD, image_bytes=4096):
        self.payload = payload
        self.image_bytes = image_bytes
        self.calls = []

    def graphql(self, op, variables, **kwargs):
        self.calls.append((op, kwargs))
        return self.payload["data"]

    def get_bytes_with_type(self, url, *, referer=None):
        return b"\x89PNG\r\n\x1a\n" + b"0" * self.image_bytes, "image/png"


class DownloadBundleTests(unittest.TestCase):
    def test_image_is_localised_and_body_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            article = download.download_article(client, "123", Path(tmp))
            self.assertTrue(article.body_md.endswith("\n"))
            self.assertIn("![](img/img_01.png)", article.body_md)
            self.assertNotIn(IMAGE_URL, article.body_md)

            md = (Path(tmp) / "123" / "article.md").read_text(encoding="utf-8")
            self.assertIn('title: "With Image"', md)
            self.assertIn("# With Image", md)
            self.assertIn("![](img/img_01.png)", md)
            self.assertTrue((Path(tmp) / "123" / "img" / "img_01.png").is_file())

            page = json.loads((Path(tmp) / "123" / "page.json").read_text(encoding="utf-8"))
            self.assertEqual(page["tweet_id"], "123")
            self.assertEqual(page["images"][0]["local_path"], "img/img_01.png")
            self.assertEqual(page["images"][0]["bytes"], 4104)

    def test_no_images_keeps_remote_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            article = download.download_article(
                client, "123", Path(tmp), download_images=False
            )
            self.assertIn(IMAGE_URL, article.body_md)
            self.assertFalse((Path(tmp) / "123" / "img").exists())

    def test_resume_skips_second_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient()
            download.download_article(client, "123", Path(tmp))
            calls_after_first = len(client.calls)
            download.download_article(client, "123", Path(tmp))
            self.assertEqual(len(client.calls), calls_after_first)

    def test_tiny_image_degrades_to_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient(image_bytes=10)
            article = download.download_article(client, "123", Path(tmp), image_delay=0)
            self.assertIn(IMAGE_URL, article.body_md)
            self.assertTrue(any("image too small" in w for w in article.warnings))
            self.assertFalse((Path(tmp) / "123" / "img").exists())

    def test_save_raw_writes_raw_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            download.download_article(
                FakeClient(), "123", Path(tmp), download_images=False, save_raw=True
            )
            raw = Path(tmp) / "123" / "raw.json"
            self.assertTrue(raw.is_file())
            self.assertIn("tweetResult", raw.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
