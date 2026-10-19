"""Unit tests for payload unwrapping / metadata extraction (坑 D4/D5/D6/D12)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from x_article_dl.download import _parse_twitter_date, parse_article_payload  # noqa: E402
from x_article_dl.errors import (  # noqa: E402
    ArticleEmptyError,
    NotAnArticleError,
    TweetUnavailableError,
)


def article_result(blocks=None, title="T"):
    return {
        "rest_id": "555",
        "title": title,
        "content_state": {
            "blocks": blocks if blocks is not None else [
                {"key": "a", "text": "hello", "type": "unstyled",
                 "data": {}, "inlineStyleRanges": [], "entityRanges": []}
            ],
            "entityMap": [],
        },
        "media_entities": [],
        "cover_media": {},
    }


def payload(result):
    return {"data": {"tweetResult": {"result": result}}}


def tweet(article=None, typename="Tweet"):
    result = {
        "__typename": typename,
        "rest_id": "123",
        "legacy": {"created_at": "Mon Oct 19 09:35:56 +0000 2026"},
        "core": {
            "user_results": {
                "result": {"core": {"screen_name": "author", "name": "Author Name"}}
            }
        },
    }
    if article is not None:
        result["article"] = {"article_results": {"result": article}}
    return result


class ParsePayloadTests(unittest.TestCase):
    def test_plain_tweet(self):
        parsed = parse_article_payload(payload(tweet(article_result())))
        self.assertEqual(parsed["title"], "T")
        self.assertEqual(parsed["article_id"], "555")
        self.assertEqual(parsed["author_handle"], "author")
        self.assertEqual(parsed["created_at"], "2026-10-19T09:35:56Z")
        self.assertEqual(len(parsed["blocks"]), 1)

    def test_visibility_wrapper(self):
        inner = tweet(article_result())
        wrapper = {"__typename": "TweetWithVisibilityResults", "tweet": inner}
        parsed = parse_article_payload(payload(wrapper))
        self.assertEqual(parsed["tweet_id"], "123")
        self.assertEqual(parsed["article_id"], "555")

    def test_tombstone(self):
        with self.assertRaises(TweetUnavailableError):
            parse_article_payload(payload({"__typename": "TweetTombstone", "tombstone": {"text": "gone"}}))

    def test_not_an_article(self):
        with self.assertRaises(NotAnArticleError):
            parse_article_payload(payload(tweet()))

    def test_empty_blocks(self):
        with self.assertRaises(ArticleEmptyError):
            parse_article_payload(payload(tweet(article_result(blocks=[]))))

    def test_missing_result(self):
        with self.assertRaises(TweetUnavailableError):
            parse_article_payload({"data": {"tweetResult": {}}})

    def test_twitter_date_parsing(self):
        self.assertEqual(_parse_twitter_date("Mon Oct 19 09:35:56 +0000 2026"), "2026-10-19T09:35:56Z")
        self.assertIsNone(_parse_twitter_date(None))
        self.assertEqual(_parse_twitter_date("not a date"), "not a date")


if __name__ == "__main__":
    unittest.main()
