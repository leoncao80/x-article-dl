"""Draft.js ``content_state`` -> Markdown.

Pure functions only: no network, no file IO.  Every function is unit-testable
with a hand-built ``content_state`` (see ``tests/test_converter.py``).

Observed in real payloads (2026-10-19, @eng_khairallah1):

* ``entityMap`` is a **list** of ``{"key", "value"}`` (pitfall C1);
* inline styles are ``"Bold"`` / ``"Italic"`` (capitalised), not ``"BOLD"``;
* ``atomic`` blocks mostly carry ``MARKDOWN`` entities whose ``data.markdown``
  already is a fenced block -- inserting it verbatim is the correct rendering;
* embeds are ``TWEET`` entities with ``data.tweetId``;
* images may come as ``IMAGE`` entities *or* via ``article_result.media_entities``.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

SUPPORTED_BLOCKS = (
    "unstyled",
    "header-one",
    "header-two",
    "header-three",
    "header-four",
    "header-five",
    "header-six",
    "unordered-list-item",
    "ordered-list-item",
    "blockquote",
    "atomic",
    "code-block",
)

LIST_BLOCKS = ("unordered-list-item", "ordered-list-item")

# 坑 C2: header-one/two/three 必须精确映射；four..six 顺延。
HEADER_PREFIX = {
    "header-one": "#",
    "header-two": "##",
    "header-three": "###",
    "header-four": "####",
    "header-five": "#####",
    "header-six": "######",
}

# 坑 C5: 行内样式 -> markdown 标记（X 实测用 "Bold"/"Italic"）
_INLINE_MARKS = {
    "BOLD": "**",
    "ITALIC": "*",
    "STRIKETHROUGH": "~~",
    "CODE": "`",
}
# 渲染嵌套顺序：外 -> 内
_MARK_ORDER = ("**", "~~", "*", "`")

_LEADING_RE = re.compile(r"^(\s*)([-+*] |\d+[.)] |[#>])")


# --------------------------------------------------------------------------- #
# entities
# --------------------------------------------------------------------------- #
def normalize_entities(content_state: dict) -> Dict[str, dict]:
    """Return ``{str(key): entity}`` whatever shape ``entityMap`` has (坑 C1)."""
    raw = (content_state or {}).get("entityMap") or {}
    out: Dict[str, dict] = {}

    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict):
                out[str(key)] = value
        return out

    for item in raw:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        if isinstance(value, dict):
            key = item.get("key")
            if key is None and "type" in item:  # 已经是扁平 {key: value}
                continue
            out[str(key)] = value
        elif "type" in item:
            # 兜底：少见形态，整条当实体，key 取 key/id
            out[str(item.get("key", len(out)))] = item
    return out


def _with_size(url: str) -> str:
    """Append X's ``?format=jpg&name=large`` sizing parameters (坑 D8)."""
    if not url:
        return url
    if "pbs.twimg.com" not in url:
        return url
    base = url.split("?", 1)[0]
    if "format=" in url:
        return url
    return base + "?format=jpg&name=large"


def _best_media_url(media: dict) -> Optional[str]:
    """Pick the highest quality URL from one media object."""
    if not isinstance(media, dict):
        return None
    info = media.get("media_info") or media
    for key in ("original_img_url", "preview_image_url", "preview_image"):
        url = info.get(key) or media.get(key)
        if url:
            return _with_size(url)
    variants = info.get("variants") or media.get("variants") or {}
    best_url = None
    best_rate = -1
    if isinstance(variants, dict):
        for items in variants.values():
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                rate = int(item.get("bitrate") or 0)
                if item.get("url") and rate >= best_rate:
                    best_url, best_rate = item["url"], rate
    return best_url


def _media_id(media: dict) -> Optional[str]:
    if not isinstance(media, dict):
        return None
    mid = media.get("media_id") or media.get("id")
    if mid is None:
        info = media.get("media_info") or {}
        mid = info.get("media_id") or info.get("id")
    return str(mid) if mid is not None else None


def media_map_from(article_result: dict) -> Dict[str, str]:
    """``media_id -> best URL`` from ``media_entities`` + ``cover_media`` (坑 D7)."""
    media: Dict[str, str] = {}
    for entity in (article_result or {}).get("media_entities") or []:
        mid = _media_id(entity)
        url = _best_media_url(entity)
        if mid and url:
            media[mid] = url
    cover = (article_result or {}).get("cover_media") or {}
    cmid = _media_id(cover)
    curl = _best_media_url(cover)
    if cmid and curl:
        media.setdefault(cmid, curl)
    return media


# --------------------------------------------------------------------------- #
# inline rendering
# --------------------------------------------------------------------------- #
def _clamp(offset, length, size: int) -> Tuple[int, int]:
    try:
        start = int(offset)
        length = int(length)
    except (TypeError, ValueError):
        return 0, 0
    start = max(0, min(start, size))
    end = max(start, min(start + max(0, length), size))
    return start, end


def _style_mark(style) -> Optional[str]:
    return _INLINE_MARKS.get(str(style or "").upper())


def _apply_marks(text: str, marks: Iterable[str]) -> str:
    active = set(marks)
    for mark in _MARK_ORDER:
        if mark in active and text:
            text = mark + text + mark
    return text


def _escape_leading(text: str) -> str:
    """Escape markdown structural characters at line starts (坑 C9)."""
    if not text:
        return text
    match = _LEADING_RE.match(text)
    if not match:
        return text
    lead = match.group(1)
    return lead + "\\" + text[len(lead) :]


def _escape_lines(text: str) -> str:
    return "\n".join(_escape_leading(line) for line in text.split("\n"))


def _link_target(entity: dict, chunk: str) -> Tuple[str, Optional[str]]:
    """Return ``(url, label_override)`` for a LINK/TWEET entity (坑 C10)."""
    data = (entity or {}).get("data") or {}
    kind = str((entity or {}).get("type") or "").upper()
    url = data.get("url") or data.get("expanded_url") or data.get("href") or ""
    if not url and data.get("tweetId"):
        url = "https://x.com/i/status/%s" % data["tweetId"]
    if not url:
        return "", None
    text = (chunk or "").strip()
    if kind == "LINK" and "t.co/" in url and text.startswith(("http://", "https://")):
        return text, None  # 坑 C10: 用可见 URL 代替短链
    return url, None


def render_inline(block: dict, entities: Dict[str, dict], *, style: bool = True) -> str:
    """Render one block's text with inline styles and link entities.

    Each character is emitted exactly once, so overlapping ranges can never
    duplicate text (坑 C11); the innermost (latest-start) link wins.
    """
    text = block.get("text") or ""
    if not text:
        return ""
    size = len(text)

    marks: List[Tuple[int, int, str]] = []
    if style:
        for rng in block.get("inlineStyleRanges") or []:
            if not isinstance(rng, dict):
                continue
            mark = _style_mark(rng.get("style"))
            if not mark:
                continue
            start, end = _clamp(rng.get("offset", 0), rng.get("length", 0), size)
            if end > start:
                marks.append((start, end, mark))

    links: List[Tuple[int, int, str]] = []
    for rng in block.get("entityRanges") or []:
        if not isinstance(rng, dict):
            continue
        key = str(rng.get("key"))
        if key not in entities:
            continue
        start, end = _clamp(rng.get("offset", 0), rng.get("length", 0), size)
        if end > start:
            links.append((start, end, key))

    if not marks and not links:
        return _escape_lines(text)

    points = {0, size}
    for start, end, _ in marks + links:
        points.update((start, end))
    points = sorted(points)

    pieces: List[Tuple[str, Optional[str], frozenset]] = []
    for idx in range(len(points) - 1):
        start, end = points[idx], points[idx + 1]
        if end <= start:
            continue
        covering = [sp for sp in links if sp[0] <= start and sp[1] >= end]
        link_key = None
        if covering:
            link_key = sorted(covering, key=lambda sp: (sp[0], -(sp[1] - sp[0])))[-1][2]
        active = frozenset(m for ms, me, m in marks if ms <= start and me >= end)
        chunk = text[start:end]
        if pieces and pieces[-1][1] == link_key and pieces[-1][2] == active:
            pieces[-1] = (pieces[-1][0] + chunk, link_key, active)
        else:
            pieces.append((chunk, link_key, active))

    rendered = []
    for chunk, link_key, active in pieces:
        piece = _apply_marks(chunk, active)
        if link_key:
            url, label = _link_target(entities.get(link_key) or {}, chunk)
            if url:
                if label:
                    piece = _apply_marks(label, active)
                piece = "[%s](%s)" % (piece, url)
        rendered.append(piece)
    return _escape_lines("".join(rendered))


# --------------------------------------------------------------------------- #
# block rendering
# --------------------------------------------------------------------------- #
def _render_list_run(run: Sequence[dict], kind: str, entities: Dict[str, dict]) -> List[str]:
    """Consecutive same-kind list items -> lines with depth indentation (坑 C3/C6)."""
    lines: List[str] = []
    counters: Dict[int, int] = {}
    for block in run:
        depth = (block.get("data") or {}).get("depth") or 0
        try:
            depth = max(0, int(depth))
        except (TypeError, ValueError):
            depth = 0
        for level in [d for d in counters if d > depth]:
            del counters[level]
        indent = "  " * depth
        if kind == "ordered-list-item":
            counters[depth] = counters.get(depth, 0) + 1
            marker = "%d. " % counters[depth]
        else:
            marker = "- "
        inline = render_inline(block, entities)
        lines.append((indent + marker + inline).rstrip() if inline.strip() else indent + marker.rstrip())
    return lines


def _entity_media_id(data: dict) -> Optional[str]:
    mid = data.get("media_id") or data.get("id") or data.get("mediaId")
    if mid is None:
        items = data.get("mediaItems") or []
        if items and isinstance(items[0], dict):
            mid = items[0].get("mediaId") or items[0].get("media_id")
    return str(mid) if mid is not None else None


def _render_atomic(
    block: dict, entities: Dict[str, dict], media: Dict[str, str]
) -> Tuple[List[str], List[str], Optional[str]]:
    """Render an ``atomic`` block -> ``(lines, warnings, used_media_id)``."""
    ranges = [r for r in block.get("entityRanges") or [] if isinstance(r, dict)]
    if not ranges:
        return [], ["atomic 块没有 entityRanges，已跳过"], None

    lines: List[str] = []
    warnings: List[str] = []
    used: Optional[str] = None

    for rng in ranges:
        entity = entities.get(str(rng.get("key"))) or {}
        data = entity.get("data") or {}
        kind = str(entity.get("type") or "").upper()

        if kind == "MARKDOWN":
            markdown = (data.get("markdown") or "").rstrip("\n")
            if markdown:
                lines.extend(markdown.split("\n"))
            continue

        if kind == "TWEET" or data.get("tweetId"):
            tweet_id = data.get("tweetId")
            if tweet_id:
                lines.append("> [嵌入推文](https://x.com/i/status/%s)" % tweet_id)
            else:
                warnings.append("atomic TWEET 实体缺少 tweetId，已跳过")
            continue

        if kind in ("IMAGE", "VIDEO", "GIF", "MEDIA"):
            media_id = _entity_media_id(data)
            url = media.get(media_id) if media_id else None
            if not url:
                url = data.get("url") or data.get("original_img_url")
            if not url:
                warnings.append(
                    "atomic 媒体 %s 找不到 URL，已跳过" % (media_id or kind)
                )
                continue
            lines.append("![](%s)" % url)
            if media_id:
                used = media_id
            if kind in ("VIDEO", "GIF"):
                warnings.append("atomic %s 仅保留封面图：%s" % (kind, media_id or url))
            continue

        warnings.append("未知 atomic entity 类型 %r，已跳过" % (kind or "?"))
    return lines, warnings, used


def render_block(
    block: dict, entities: Dict[str, dict], media: Optional[Dict[str, str]] = None
) -> List[str]:
    """Render a non-list block into markdown lines (may be empty)."""
    media = media or {}
    kind = block.get("type") or "unstyled"

    if kind in HEADER_PREFIX:
        inline = render_inline(block, entities)
        if not inline.strip():
            return []
        return ["%s %s" % (HEADER_PREFIX[kind], inline.strip())]

    if kind == "blockquote":
        inline = render_inline(block, entities)
        lines = inline.split("\n") if inline else [""]
        # 坑 C4: 每一行都要加 "> "
        return [("> " + line).rstrip() if line.strip() else ">" for line in lines]

    if kind == "code-block":
        return ["```", (block.get("text") or "").rstrip("\n"), "```"]

    if kind == "atomic":
        return _render_atomic(block, entities, media)[0]

    inline = render_inline(block, entities)
    return [inline] if inline.strip() else []


def blocks_to_markdown(
    blocks: Sequence[dict],
    entities: Dict[str, dict],
    media: Optional[Dict[str, str]] = None,
) -> Tuple[str, List[str], List[str]]:
    """Draft.js blocks -> ``(body_md, used_media_ids, warnings)``.

    Output invariant: every block yields a fragment ending in ``"\\n"`` and the
    result is ``"\\n".join(parts).strip() + "\\n"`` -- blocks are therefore
    separated by exactly one blank line (坑 C3).
    """
    media = media or {}
    parts: List[str] = []
    warnings: List[str] = []
    used_media: List[str] = []

    index = 0
    total = len(blocks)
    while index < total:
        block = blocks[index] if isinstance(blocks[index], dict) else {}
        kind = block.get("type") or "unstyled"

        if kind in LIST_BLOCKS:
            end = index
            run: List[dict] = []
            while (
                end < total
                and isinstance(blocks[end], dict)
                and (blocks[end].get("type") or "") == kind
            ):
                run.append(blocks[end])
                end += 1
            lines = _render_list_run(run, kind, entities)
            if lines:
                parts.append("\n".join(lines) + "\n")
            index = end
            continue

        if kind not in SUPPORTED_BLOCKS:
            warnings.append("未知 block 类型 %r，已按段落降级" % kind)
            lines = render_block(dict(block, type="unstyled"), entities, media)
        elif kind == "atomic":
            lines, block_warnings, media_id = _render_atomic(block, entities, media)
            warnings.extend(block_warnings)
            if media_id and media_id not in used_media:
                used_media.append(media_id)
        else:
            lines = render_block(block, entities, media)

        if lines:
            parts.append("\n".join(lines) + "\n")
        index += 1

    body = "\n".join(parts).strip() + "\n"
    return body, used_media, warnings


def plain_text_from_blocks(
    blocks: Sequence[dict], entities: Optional[Dict[str, dict]] = None
) -> str:
    """Plain text (for ``chars``, 坑 D10) -- no markdown syntax counted."""
    entities = entities or {}
    chunks: List[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if (block.get("type") or "") == "atomic":
            for rng in block.get("entityRanges") or []:
                entity = entities.get(str(rng.get("key"))) or {}
                markdown = (entity.get("data") or {}).get("markdown") or ""
                if markdown:
                    chunks.append(markdown)
            continue
        text = block.get("text") or ""
        if text.strip():
            chunks.append(text)
    return "\n".join(chunks)


def convert_article(
    article_result: dict,
) -> Tuple[str, List[Tuple[str, str]], List[str]]:
    """``article_result`` -> ``(body_md, [(media_id, url)], warnings)``.

    If the first block repeats the article title (坑 C8), it is dropped because
    the bundle template already renders ``# <title>``.
    """
    state = (article_result or {}).get("content_state") or {}
    blocks = [b for b in (state.get("blocks") or []) if isinstance(b, dict)]
    entities = normalize_entities(state)
    media = media_map_from(article_result)
    title = ((article_result or {}).get("title") or "").strip()

    if blocks and title and (blocks[0].get("type") == "header-one"):
        first_text = (blocks[0].get("text") or "").strip()
        if first_text and first_text.casefold() == title.casefold():
            blocks = blocks[1:]

    body, used, warnings = blocks_to_markdown(blocks, entities, media)
    pairs = [(mid, media[mid]) for mid in used if mid in media]
    return body, pairs, warnings


__all__ = [
    "SUPPORTED_BLOCKS",
    "blocks_to_markdown",
    "convert_article",
    "media_map_from",
    "normalize_entities",
    "plain_text_from_blocks",
    "render_block",
    "render_inline",
]
