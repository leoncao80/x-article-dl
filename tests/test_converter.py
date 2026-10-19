"""Unit tests for the Draft.js -> Markdown converter (design doc 坑 C1-C12)."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from x_article_dl.converter import (  # noqa: E402
    blocks_to_markdown,
    convert_article,
    media_map_from,
    normalize_entities,
    plain_text_from_blocks,
    render_inline,
)


def block(text: str, kind: str = "unstyled", *, depth: int = None, entities=None, styles=None):
    data = {} if depth is None else {"depth": depth}
    return {
        "key": "k" + str(abs(hash((text, kind, depth))) % 10000),
        "text": text,
        "type": kind,
        "data": data,
        "inlineStyleRanges": list(styles or []),
        "entityRanges": list(entities or []),
    }


def md(blocks, entities=None, media=None):
    body, used, warnings = blocks_to_markdown(blocks, entities or {}, media or {})
    return body, used, warnings


class HeaderTests(unittest.TestCase):
    """坑 C2: header-one/two/three 必须精确映射（核心优势，不许退化）。"""

    def test_four_header_levels(self):
        body, _, _ = md(
            [
                block("One", "header-one"),
                block("Two", "header-two"),
                block("Three", "header-three"),
                block("Four", "header-four"),
            ]
        )
        self.assertIn("# One\n", body)
        self.assertIn("## Two\n", body)
        self.assertIn("### Three\n", body)
        self.assertIn("#### Four\n", body)
        self.assertNotIn("#### Two", body)


class ListTests(unittest.TestCase):
    """坑 C3/C6: 连续合并、depth 缩进、有序列表每层独立编号。"""

    def test_nested_list_indentation(self):
        body, _, _ = md(
            [
                block("top", "unordered-list-item", depth=0),
                block("child", "unordered-list-item", depth=1),
                block("grandchild", "unordered-list-item", depth=2),
            ]
        )
        self.assertIn("- top\n  - child\n    - grandchild\n", body)

    def test_ordered_list_numbers_restart_per_depth(self):
        body, _, _ = md(
            [
                block("a", "ordered-list-item", depth=0),
                block("a1", "ordered-list-item", depth=1),
                block("a2", "ordered-list-item", depth=1),
                block("b", "ordered-list-item", depth=0),
            ]
        )
        self.assertIn("1. a\n  1. a1\n  2. a2\n2. b\n", body)

    def test_list_run_is_broken_by_paragraph(self):
        body, _, _ = md(
            [
                block("one", "unordered-list-item"),
                block("middle paragraph"),
                block("two", "unordered-list-item"),
            ]
        )
        self.assertIn("- one\n\nmiddle paragraph\n\n- two\n", body)


class QuoteTests(unittest.TestCase):
    """坑 C4: blockquote 每一行都要加 '> '。"""

    def test_multiline_quote(self):
        body, _, _ = md([block("first line\nsecond line", "blockquote")])
        self.assertIn("> first line\n> second line\n", body)


class InlineTests(unittest.TestCase):
    """坑 C5/C10/C11: 行内样式、LINK 实体、重叠区间不重复输出。"""

    def test_bold_and_italic(self):
        b = block(
            "bold italic",
            styles=[
                {"offset": 0, "length": 4, "style": "Bold"},
                {"offset": 5, "length": 6, "style": "Italic"},
            ],
        )
        self.assertEqual(render_inline(b, {}), "**bold** *italic*")

    def test_link_entity(self):
        entities = {"0": {"type": "LINK", "data": {"url": "https://example.com"}}}
        b = block("see site", entities=[{"offset": 4, "length": 4, "key": 0}])
        self.assertEqual(render_inline(b, entities), "see [site](https://example.com)")

    def test_overlapping_ranges_do_not_duplicate_text(self):
        entities = {
            "0": {"type": "LINK", "data": {"url": "https://a.example"}},
            "1": {"type": "LINK", "data": {"url": "https://b.example"}},
        }
        b = block(
            "abcdef",
            entities=[
                {"offset": 0, "length": 4, "key": 0},
                {"offset": 2, "length": 4, "key": 1},
            ],
        )
        rendered = render_inline(b, entities)
        # 去掉链接目标后，可见文本必须恰好是原文一次（不重复、不丢字）
        visible = re.sub(r"\]\([^)]*\)", "", rendered).replace("[", "")
        self.assertEqual(visible, "abcdef")

    def test_leading_hash_is_escaped(self):
        self.assertEqual(render_inline(block("# not a heading"), {}), "\\# not a heading")


class EntityMapTests(unittest.TestCase):
    """坑 C1: entityMap 是 list 形态，必须能解析出链接。"""

    def test_list_form_entity_map(self):
        state = {
            "blocks": [],
            "entityMap": [{"key": "0", "value": {"type": "LINK", "data": {"url": "u"}}}],
        }
        entities = normalize_entities(state)
        self.assertEqual(entities["0"]["data"]["url"], "u")

    def test_dict_form_still_supported(self):
        state = {"blocks": [], "entityMap": {"0": {"type": "LINK", "data": {"url": "u"}}}}
        self.assertEqual(normalize_entities(state)["0"]["type"], "LINK")


class AtomicTests(unittest.TestCase):
    """坑 C7/D7: atomic 图片块产出 ![](url) 占位。"""

    def test_image_atomic(self):
        entities = {"0": {"type": "IMAGE", "data": {"media_id": 12345}}}
        media = {"12345": "https://pbs.twimg.com/media/x.jpg?format=jpg&name=large"}
        body, used, warnings = md(
            [block(" ", "atomic", entities=[{"offset": 0, "length": 1, "key": 0}])],
            entities,
            media,
        )
        self.assertIn("![](https://pbs.twimg.com/media/x.jpg?format=jpg&name=large)", body)
        self.assertEqual(used, ["12345"])
        self.assertEqual(warnings, [])

    def test_markdown_atomic_is_inserted_verbatim(self):
        code = "```plaintext\nhello\nworld\n```"
        entities = {"0": {"type": "MARKDOWN", "data": {"markdown": code}}}
        body, _, warnings = md(
            [block(" ", "atomic", entities=[{"offset": 0, "length": 1, "key": 0}])],
            entities,
        )
        self.assertIn("```plaintext\nhello\nworld\n```", body)
        self.assertEqual(warnings, [])

    def test_tweet_embed(self):
        entities = {"0": {"type": "TWEET", "data": {"tweetId": "999"}}}
        body, _, _ = md(
            [block(" ", "atomic", entities=[{"offset": 0, "length": 1, "key": 0}])],
            entities,
        )
        self.assertIn("https://x.com/i/status/999", body)

    def test_missing_media_url_warns_and_skips(self):
        entities = {"0": {"type": "IMAGE", "data": {"media_id": "404"}}}
        body, _, warnings = md(
            [block(" ", "atomic", entities=[{"offset": 0, "length": 1, "key": 0}])],
            entities,
        )
        self.assertEqual(body, "\n")
        self.assertEqual(len(warnings), 1)


class FallbackTests(unittest.TestCase):
    """坑 C12: 未知 block 类型降级为段落 + warning，不丢内容。"""

    def test_unknown_block_becomes_paragraph(self):
        body, _, warnings = md([block("divider payload", "divider")])
        self.assertIn("divider payload", body)
        self.assertEqual(len(warnings), 1)
        self.assertIn("divider", warnings[0])


class InvariantTests(unittest.TestCase):
    """输出不变量：每块以 \\n 结尾，最终 strip + \\n；blocks 之间恰一空行。"""

    def test_blocks_separated_by_one_blank_line(self):
        body, _, _ = md([block("a"), block("b")])
        self.assertEqual(body, "a\n\nb\n")

    def test_plain_text_excludes_markdown_syntax(self):
        blocks = [block("## heading", "header-two"), block("body text")]
        self.assertEqual(plain_text_from_blocks(blocks), "## heading\nbody text")


class ConvertArticleTests(unittest.TestCase):
    """坑 C8: 首个 header-one 与 title 重复时去重。"""

    def test_duplicate_title_header_is_dropped(self):
        article_result = {
            "title": "My Title",
            "content_state": {
                "blocks": [
                    block("My Title", "header-one"),
                    block("Body."),
                ],
                "entityMap": [],
            },
        }
        body, used, _ = convert_article(article_result)
        self.assertEqual(body, "Body.\n")
        self.assertEqual(used, [])

    def test_media_map_from_cover_and_entities(self):
        article_result = {
            "media_entities": [
                {
                    "media_id": 42,
                    "media_info": {"original_img_url": "https://pbs.twimg.com/media/a.jpg"},
                }
            ],
            "cover_media": {
                "media_id": "7",
                "media_info": {"original_img_url": "https://pbs.twimg.com/media/b.jpg"},
            },
        }
        media = media_map_from(article_result)
        self.assertIn("?format=jpg&name=large", media["42"])
        self.assertIn("?format=jpg&name=large", media["7"])


if __name__ == "__main__":
    unittest.main()
