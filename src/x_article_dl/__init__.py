"""x-article-dl — download every X (Twitter) Article of a user as clean Markdown.

Public API re-exports.  Everything here is import-safe: no network, no file IO.
"""
from __future__ import annotations

from .auth import DEFAULT_PROXY, DEFAULT_UA, Credentials, Limits, XClient
from .converter import (
    blocks_to_markdown,
    convert_article,
    media_map_from,
    normalize_entities,
    plain_text_from_blocks,
)
from .download import (
    Article,
    ImageAsset,
    download_article,
    fetch_article,
    parse_article_payload,
    render_article_markdown,
    write_bundle,
)
from .errors import (
    ArticleEmptyError,
    AuthError,
    ConfigError,
    GraphQLAPIError,
    ImageDownloadError,
    NetworkError,
    NotAnArticleError,
    RateLimitError,
    TweetUnavailableError,
    UserNotFoundError,
    XadlError,
)
from .listing import ArticleRef, UserInfo, iter_articles, list_articles, resolve_user_id

__version__ = "0.1.2"

__all__ = [
    "__version__",
    # auth
    "DEFAULT_PROXY",
    "DEFAULT_UA",
    "Credentials",
    "Limits",
    "XClient",
    # listing
    "ArticleRef",
    "UserInfo",
    "iter_articles",
    "list_articles",
    "resolve_user_id",
    # download
    "Article",
    "ImageAsset",
    "download_article",
    "fetch_article",
    "parse_article_payload",
    "render_article_markdown",
    "write_bundle",
    # converter
    "blocks_to_markdown",
    "convert_article",
    "media_map_from",
    "normalize_entities",
    "plain_text_from_blocks",
    # errors
    "XadlError",
    "ConfigError",
    "AuthError",
    "NetworkError",
    "RateLimitError",
    "GraphQLAPIError",
    "UserNotFoundError",
    "TweetUnavailableError",
    "NotAnArticleError",
    "ArticleEmptyError",
    "ImageDownloadError",
]
