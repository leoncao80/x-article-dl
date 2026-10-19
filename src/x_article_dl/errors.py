"""Exception hierarchy for x-article-dl.

Every exception carries an ``exit_code`` so the CLI can map failures onto a
stable process exit status.  Library code must raise these, never call
``sys.exit()`` (see design doc, pitfall A6).
"""
from __future__ import annotations

from typing import Optional


class XadlError(Exception):
    """Base class for every error raised by x-article-dl."""

    exit_code = 1


class ConfigError(XadlError):
    """Bad arguments, missing cookie file, gallery-dl not found, ..."""

    exit_code = 2


class AuthError(XadlError):
    """401/403, ct0 mismatch, unreadable bearer token."""

    exit_code = 2


class NetworkError(XadlError):
    """Proxy unreachable, TLS failure, timeout."""

    exit_code = 1


class RateLimitError(NetworkError):
    """HTTP 429.  ``reset_at`` is a unix timestamp when known."""

    exit_code = 1

    def __init__(self, message: str = "rate limited", reset_at: Optional[float] = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


class GraphQLAPIError(XadlError):
    """The ``errors`` array of a GraphQL response was non-empty."""

    exit_code = 1

    def __init__(self, message: str, errors=None) -> None:
        super().__init__(message)
        self.errors = list(errors or [])


class UserNotFoundError(XadlError):
    """UserByScreenName returned UserUnavailable / empty data."""

    exit_code = 1


class TweetUnavailableError(XadlError):
    """TweetTombstone / TweetUnavailable / deleted tweet."""

    exit_code = 1


class NotAnArticleError(XadlError):
    """The tweet exists but carries no ``article`` payload."""

    exit_code = 1


class ArticleEmptyError(XadlError):
    """``article.content_state.blocks`` is empty."""

    exit_code = 1


class ImageDownloadError(XadlError):
    """A single image failed; by default callers degrade instead of raising."""

    exit_code = 1


__all__ = [
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
