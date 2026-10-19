"""Fetch a single X Article, download its images and write the bundle.

Layering (design doc §2.3 / §5):

``fetch_article``      -> pure data, no disk IO
``download_article``   -> fetch + images + ``write_bundle``
``write_bundle``       -> atomic ``article.md`` / ``page.json`` (+ ``raw.json``)
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from .converter import (
    convert_article,
    media_map_from,
    normalize_entities,
    plain_text_from_blocks,
)
from .errors import (
    ArticleEmptyError,
    AuthError,
    ImageDownloadError,
    NetworkError,
    NotAnArticleError,
    RateLimitError,
    TweetUnavailableError,
    XadlError,
)

logger = logging.getLogger(__name__)

# TweetResultByRestId: withArticleRichContentState=true 是拿到全篇正文的
# **唯一**开关（坑 D1）。features/fieldToggles 常量集中在 _endpoints.py。
TWEET_VARIABLES = {
    "withCommunity": False,
    "includePromotedContent": False,
    "withVoice": False,
}

_CTYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_URL_EXT_RE = re.compile(r"\.(jpe?g|png|webp|gif)$", re.IGNORECASE)
_MIN_IMAGE_BYTES = 2048  # 小于此值基本是占位图（坑 D14）


# --------------------------------------------------------------------------- #
# data model
# --------------------------------------------------------------------------- #
@dataclass
class ImageAsset:
    index: int  # 1-based -> img_01.jpg；封面为 0 -> img_00.jpg
    media_id: str
    remote_url: str
    local_path: Optional[str] = None  # 相对 bundle："img/img_01.jpg"；失败为 None
    is_cover: bool = False
    bytes: int = 0


@dataclass
class Article:
    tweet_id: str
    tweet_url: str
    article_id: str
    title: str
    author_handle: str
    author_name: str
    created_at: Optional[str]
    body_md: str
    blocks_count: int
    images: List[ImageAsset]
    chars: int
    plain_text: str
    warnings: List[str]
    fetched_at: str
    raw: dict = field(repr=False, default_factory=dict)
    cover: Optional[ImageAsset] = field(default=None, repr=False)


# --------------------------------------------------------------------------- #
# payload parsing (pure, unit-testable)
# --------------------------------------------------------------------------- #
def _parse_twitter_date(value: Optional[str]) -> Optional[str]:
    """``Mon Oct 19 09:35:56 +0000 2026`` -> ISO8601 UTC (坑 D12)."""
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y")
    except (TypeError, ValueError):
        return value
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _author_of(tweet: dict) -> Tuple[str, str]:
    core = tweet.get("core") or {}
    user = ((core.get("user_results") or {}).get("result")) or {}
    user_core = user.get("core") or {}
    user_legacy = user.get("legacy") or {}
    handle = user_core.get("screen_name") or user_legacy.get("screen_name") or ""
    name = user_core.get("name") or user_legacy.get("name") or ""
    return handle, name


def parse_article_payload(payload: dict) -> dict:
    """Unwrap the GraphQL response down to the ``article_results.result`` dict.

    Handles the ``TweetWithVisibilityResults`` wrapper (坑 D4), tombstones and
    non-article tweets (坑 D5), and empty ``blocks`` (坑 D6).
    """
    data = (payload or {}).get("data", payload) or {}
    result = ((data.get("tweetResult") or {}).get("result")) or {}
    if not result:
        raise TweetUnavailableError("响应里没有 tweetResult.result")

    typename = result.get("__typename") or ""
    if typename in ("TweetTombstone", "TweetUnavailable"):
        reason = (result.get("tombstone") or {}).get("text") or typename
        raise TweetUnavailableError("推文不可用：%s" % reason)

    tweet = result.get("tweet", result) if typename == "TweetWithVisibilityResults" else result.get("tweet", result)
    if not isinstance(tweet, dict):
        raise TweetUnavailableError("tweetResult 结构异常")

    article = tweet.get("article")
    if not article:
        raise NotAnArticleError("这条推文不是 Article（没有 article 字段）")

    article_result = ((article.get("article_results") or {}).get("result")) or {}
    if not article_result:
        raise NotAnArticleError("article_results.result 为空")

    state = article_result.get("content_state") or {}
    blocks = state.get("blocks") or []
    if not blocks:
        raise ArticleEmptyError("article 的 content_state.blocks 为空")

    handle, name = _author_of(tweet)
    legacy = tweet.get("legacy") or {}

    return {
        "article_result": article_result,
        "tweet": tweet,
        "title": (article_result.get("title") or "").strip(),
        "content_state": state,
        "blocks": blocks,
        "entities": normalize_entities(state),
        "media_entities": article_result.get("media_entities") or [],
        "cover_media": article_result.get("cover_media") or {},
        "media_map": media_map_from(article_result),
        "article_id": str(article_result.get("rest_id") or article_result.get("id") or ""),
        "tweet_id": str(tweet.get("rest_id") or ""),
        "author_handle": handle,
        "author_name": name,
        "created_at": _parse_twitter_date(legacy.get("created_at")),
    }


# --------------------------------------------------------------------------- #
# fetch
# --------------------------------------------------------------------------- #
def _fetch_payload(client, tweet_id: str, *, use_guest: bool) -> dict:
    """Try guest first, fall back to the session (or the reverse, 坑 D1)."""
    variables = dict(TWEET_VARIABLES, tweetId=str(tweet_id))
    order = [True, False] if use_guest else [False, True]
    last: Optional[Exception] = None
    for guest in order:
        try:
            logger.debug("TweetResultByRestId %s (guest=%s)", tweet_id, guest)
            return client.graphql(
                "TweetResultByRestId", variables, session=not guest, guest=guest
            )
        except (AuthError, RateLimitError, NetworkError) as exc:
            last = exc
            logger.debug("guest=%s 失败（%s），换另一种身份重试", guest, exc)
    if last is not None:
        raise last
    raise NetworkError("请求 TweetResultByRestId 失败")


def fetch_article(client, tweet_id: str, *, use_guest: bool = True) -> Article:
    """Fetch one article; no disk IO, no image download."""
    payload = _fetch_payload(client, tweet_id, use_guest=use_guest)
    parsed = parse_article_payload(payload)

    blocks = parsed["blocks"]
    entities = parsed["entities"]
    body_md, media_pairs, warnings = convert_article(parsed["article_result"])
    plain_text = plain_text_from_blocks(blocks, entities)

    images: List[ImageAsset] = []
    for index, (media_id, url) in enumerate(media_pairs, start=1):
        images.append(
            ImageAsset(index=index, media_id=str(media_id), remote_url=url)
        )

    cover_id = str(
        (parsed["cover_media"].get("media_id") or parsed["cover_media"].get("id") or "")
    )
    cover_url = parsed["media_map"].get(cover_id) if cover_id else None
    used_ids = {asset.media_id for asset in images}
    cover = None
    if cover_id and cover_url and cover_id not in used_ids:
        cover = ImageAsset(
            index=0, media_id=cover_id, remote_url=cover_url, is_cover=True
        )

    if not body_md.strip():
        raise ArticleEmptyError("converter 产出的正文为空")

    return Article(
        tweet_id=str(tweet_id),
        tweet_url="https://x.com/%s/status/%s" % (
            parsed["author_handle"] or "i",
            tweet_id,
        ),
        article_id=parsed["article_id"],
        title=parsed["title"] or "(untitled)",
        author_handle=parsed["author_handle"],
        author_name=parsed["author_name"],
        created_at=parsed["created_at"],
        body_md=body_md,
        blocks_count=len(blocks),
        images=images,
        chars=len(plain_text),
        plain_text=plain_text,
        warnings=warnings,
        fetched_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        raw={"data": payload.get("data", payload)},
        cover=cover,
    )


# --------------------------------------------------------------------------- #
# images
# --------------------------------------------------------------------------- #
def _ext_for(content_type: Optional[str], url: str) -> str:
    """Content-Type first, URL suffix second (坑 D9)."""
    if content_type:
        ext = _CTYPE_EXT.get(content_type.split(";")[0].strip().lower())
        if ext:
            return ext
    match = _URL_EXT_RE.search((url or "").split("?", 1)[0])
    if match:
        suffix = match.group(1).lower()
        return ".jpg" if suffix in ("jpeg", "jpg") else "." + suffix
    return ".jpg"


def _download_image(
    client, url: str, img_dir: Path, index: int, *, referer: Optional[str]
) -> Tuple[str, int]:
    """Download one image -> ``(relative_path, bytes)``."""
    data, content_type = client.get_bytes_with_type(url, referer=referer)
    if len(data) < _MIN_IMAGE_BYTES:
        raise ImageDownloadError("图片过小（%d 字节），疑似占位图" % len(data))
    ext = _ext_for(content_type, url)
    dest = img_dir / ("img_%02d%s" % (index, ext))
    tmp = dest.with_name(dest.name + ".tmp")
    img_dir.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return str(dest.relative_to(img_dir.parent)), len(data)


# --------------------------------------------------------------------------- #
# bundle writing
# --------------------------------------------------------------------------- #
def _tool_version() -> str:
    try:
        from importlib.metadata import version

        return version("x-article-dl")
    except Exception:  # 未安装（源码目录直接运行）
        return "0.1.0"


def _yaml_str(value: Optional[str]) -> str:
    return json.dumps(value or "", ensure_ascii=False)


def render_article_markdown(article: Article) -> str:
    """YAML frontmatter + ``# Title`` + body (images already local)."""
    image_count = len(article.images) + (1 if article.cover else 0)
    lines = [
        "---",
        "title: %s" % _yaml_str(article.title),
        "tweet_id: %s" % _yaml_str(article.tweet_id),
        "tweet_url: %s" % _yaml_str(article.tweet_url),
        "article_id: %s" % _yaml_str(article.article_id),
        "author: %s" % _yaml_str("@" + article.author_handle if article.author_handle else ""),
        "author_name: %s" % _yaml_str(article.author_name),
        "created_at: %s" % _yaml_str(article.created_at),
        "fetched_at: %s" % _yaml_str(article.fetched_at),
        "chars: %d" % article.chars,
        "images: %d" % image_count,
    ]
    if article.cover and article.cover.local_path:
        lines.append("cover: %s" % _yaml_str(article.cover.local_path))
    lines += [
        "source: %s" % _yaml_str("x-article-dl " + _tool_version()),
        "---",
        "",
        "# %s" % article.title,
        "",
        article.body_md.rstrip("\n"),
        "",
    ]
    return "\n".join(lines)


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_bundle(article: Article, bundle_dir: Path, *, save_raw: bool = False) -> Path:
    """Write ``article.md`` + ``page.json`` (+ ``raw.json``) atomically."""
    bundle_dir = Path(bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    page = {
        "tweet_id": article.tweet_id,
        "tweet_url": article.tweet_url,
        "article_id": article.article_id,
        "title": article.title,
        "author": article.author_handle,
        "author_name": article.author_name,
        "created_at": article.created_at,
        "fetched_at": article.fetched_at,
        "chars": article.chars,
        "blocks": article.blocks_count,
        "images": [
            {
                "index": asset.index,
                "media_id": asset.media_id,
                "remote_url": asset.remote_url,
                "local_path": asset.local_path,
                "is_cover": asset.is_cover,
                "bytes": asset.bytes,
            }
            for asset in ([article.cover] if article.cover else []) + article.images
        ],
        "warnings": article.warnings,
        "source": "x-article-dl " + _tool_version(),
    }

    _atomic_write(bundle_dir / "article.md", render_article_markdown(article))
    _atomic_write(bundle_dir / "page.json", json.dumps(page, ensure_ascii=False, indent=2) + "\n")
    if save_raw:
        _atomic_write(
            bundle_dir / "raw.json",
            json.dumps(article.raw, ensure_ascii=False, indent=1) + "\n",
        )
    return bundle_dir / "article.md"


def _load_cached(bundle_dir: Path) -> Optional[Article]:
    """Reuse an existing bundle when ``page.json`` + non-empty ``article.md`` exist."""
    page_path = bundle_dir / "page.json"
    md_path = bundle_dir / "article.md"
    if not page_path.is_file() or not md_path.is_file() or md_path.stat().st_size == 0:
        return None
    try:
        page = json.loads(page_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("page.json 损坏，将重下：%s（%s）", page_path, exc)
        return None

    images: List[ImageAsset] = []
    cover = None
    for item in page.get("images") or []:
        try:
            asset = ImageAsset(
                index=int(item.get("index") or 0),
                media_id=str(item.get("media_id") or ""),
                remote_url=str(item.get("remote_url") or ""),
                local_path=item.get("local_path"),
                is_cover=bool(item.get("is_cover")),
                bytes=int(item.get("bytes") or 0),
            )
        except (TypeError, ValueError):
            continue
        if asset.is_cover:
            cover = asset
        else:
            images.append(asset)

    return Article(
        tweet_id=str(page.get("tweet_id") or bundle_dir.name),
        tweet_url=str(page.get("tweet_url") or ""),
        article_id=str(page.get("article_id") or ""),
        title=str(page.get("title") or "(untitled)"),
        author_handle=str(page.get("author") or ""),
        author_name=str(page.get("author_name") or ""),
        created_at=page.get("created_at"),
        body_md=md_path.read_text(encoding="utf-8"),
        blocks_count=int(page.get("blocks") or 0),
        images=images,
        chars=int(page.get("chars") or 0),
        plain_text="",
        warnings=list(page.get("warnings") or []),
        fetched_at=str(page.get("fetched_at") or ""),
        cover=cover,
    )


def download_article(
    client,
    tweet_id: str,
    outdir: Path,
    *,
    tweet_url: Optional[str] = None,
    force: bool = False,
    download_images: bool = True,
    save_raw: bool = False,
    image_delay: float = 0.2,
) -> Article:
    """Fetch, download images and write ``<outdir>/<tweet_id>/``.

    An existing bundle is reused unless ``force`` (坑 D13).  A failed image only
    produces a warning; the download never aborts because of it (坑 D14).
    """
    outdir = Path(outdir)
    bundle_dir = outdir / str(tweet_id)

    if not force:
        cached = _load_cached(bundle_dir)
        if cached is not None:
            logger.info("已存在，跳过：%s", bundle_dir)
            if tweet_url:
                cached.tweet_url = tweet_url
            return cached

    article = fetch_article(client, tweet_id)
    if tweet_url:
        article.tweet_url = tweet_url

    assets = ([article.cover] if article.cover else []) + list(article.images)
    if download_images and assets:
        img_dir = bundle_dir / "img"
        for asset in assets:
            try:
                relative, size = _download_image(
                    client, asset.remote_url, img_dir, asset.index, referer=article.tweet_url
                )
                asset.local_path = relative
                asset.bytes = size
                article.body_md = article.body_md.replace(
                    "](%s)" % asset.remote_url, "](%s)" % relative
                )
            except XadlError as exc:
                article.warnings.append("图片下载失败 %s：%s" % (asset.remote_url, exc))
                logger.warning("图片失败 %s：%s", asset.remote_url, exc)
            if image_delay:
                time.sleep(image_delay)

    write_bundle(article, bundle_dir, save_raw=save_raw)
    return article


__all__ = [
    "Article",
    "ImageAsset",
    "download_article",
    "fetch_article",
    "parse_article_payload",
    "render_article_markdown",
    "write_bundle",
]
