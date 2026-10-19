"""Timeline listing: resolve a handle, then page through the Articles tab.

All calls are session-authenticated (``x-twitter-auth-type: OAuth2Session``,
pitfall L2); guest tokens never work for this timeline.
"""
from __future__ import annotations

import itertools
import logging
import time
from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple

from . import _endpoints, auth
from .errors import GraphQLAPIError, UserNotFoundError
from .auth import XClient

logger = logging.getLogger(__name__)

ARTICLES_OP = "UserArticlesTweets"
USER_OP = "UserByScreenName"


@dataclass(frozen=True)
class UserInfo:
    user_id: str
    screen_name: str  # authoritative casing, straight from the API (pitfall L6)
    name: str = ""


@dataclass(frozen=True)
class ArticleRef:
    tweet_id: str
    url: str
    sort_index: int  # 0-based, API order (newest first)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _unwrap(payload: dict, op: str) -> dict:
    errors = payload.get("errors") or []
    if errors:
        raise GraphQLAPIError(
            "%s: %s" % (op, errors[0].get("message", "unknown GraphQL error")), errors
        )
    if "data" not in payload:
        raise GraphQLAPIError("%s: response is missing the data field" % op, errors)
    return payload["data"]


def _graphql(client: XClient, op: str, variables: dict, *, qid: Optional[str] = None) -> dict:
    """Call an operation, allowing an explicit queryId override (pitfall L1)."""
    if qid is None:
        return client.graphql(op, variables, session=True)
    url = _endpoints.build_graphql_url(op, variables, qid=qid)
    return _unwrap(client.get_json(url, session=True), op)


def _normalize_handle(handle: str) -> str:
    return handle.strip().lstrip("@")


# --------------------------------------------------------------------------- #
# user resolution
# --------------------------------------------------------------------------- #
def resolve_user_id(client: XClient, handle: str) -> UserInfo:
    """Look up ``userId`` + canonical ``screen_name`` via UserByScreenName."""
    screen_name = _normalize_handle(handle)
    if not screen_name:
        raise UserNotFoundError("handle is empty")

    # Prefer the queryId currently used by gallery-dl, fall back to the explicit
    # constant when it cannot be read (pitfalls L1/L9).
    qid = auth.read_query_id(USER_OP, getattr(client, "bearer_path", None))
    if qid:
        logger.debug("UserByScreenName queryId from gallery-dl: %s", qid)
    data = _graphql(client, USER_OP, {"screen_name": screen_name}, qid=qid)

    result = (((data.get("user") or {}).get("result")) or {})
    typename = result.get("__typename")
    if typename in ("UserUnavailable", "UserTombstone"):
        raise UserNotFoundError("user unavailable: @%s (%s)" % (screen_name, typename))
    rest_id = result.get("rest_id")
    if not rest_id:
        raise UserNotFoundError(
            "no user_id returned for @%s; pass --user-id to skip this lookup" % screen_name
        )

    legacy = result.get("legacy") or {}
    core = result.get("core") or {}
    canonical = (
        legacy.get("screen_name")
        or core.get("screen_name")
        or screen_name
    )
    name = legacy.get("name") or core.get("name") or ""
    logger.info("resolved @%s → user_id=%s", canonical, rest_id)
    return UserInfo(user_id=str(rest_id), screen_name=canonical, name=name)


# --------------------------------------------------------------------------- #
# timeline parsing
# --------------------------------------------------------------------------- #
def _iter_entry_dicts(instruction: dict) -> Iterator[dict]:
    """Yield entry-like dicts from every instruction shape we know of (pitfall L8)."""
    itype = instruction.get("type") or instruction.get("__typename") or ""
    if itype in ("TimelineAddEntries", "TimelineClearCache"):
        for entry in instruction.get("entries") or []:
            yield entry
    elif itype == "TimelineReplaceEntry":
        entry = ((instruction.get("replaceEntry") or {}).get("entry")) or {}
        if entry:
            yield entry
    elif itype == "TimelinePinEntry":
        entry = instruction.get("entry") or {}
        if entry:
            yield entry
    elif itype in ("TimelineAddToModule", "TimelineTerminateTimeline"):
        for item in instruction.get("moduleItems") or []:
            if isinstance(item, dict):
                yield item
    else:  # fallback: any instruction carrying entries
        for entry in instruction.get("entries") or []:
            yield entry


def _tweet_id_from_entry(entry: dict) -> Optional[str]:
    """Extract a tweet id from an entry, ignoring cursor/module noise (pitfall L7)."""
    entry_id = entry.get("entryId") or entry.get("entry_id") or entry.get("itemId") or ""
    if entry_id.startswith("tweet-"):
        return entry_id.split("-", 1)[1]
    if "tweet-" in entry_id and entry_id.startswith(("profile-grid-", "profile-conversation-")):
        return entry_id.rsplit("tweet-", 1)[1]
    # module items / conversation entries only expose rest_id deeper down
    item = entry.get("item") or {}
    content = (item.get("itemContent") or entry.get("content") or {}) if isinstance(item, dict) else {}
    tweet_results = (content or {}).get("tweet_results") or {}
    rest_id = ((tweet_results.get("result") or {}) if isinstance(tweet_results, dict) else {}).get("rest_id")
    if rest_id:
        return str(rest_id)
    return None


def _parse_entries(payload: dict) -> Tuple[List[str], Optional[str]]:
    """Parse one timeline page into ``([tweet_id, ...], next_cursor)``.

    Handles both ``result.timeline.timeline.instructions`` and the older
    ``result.timeline.instructions`` layout (pitfall L8).
    """
    data = payload.get("data", payload)
    result = (((data.get("user") or {}).get("result")) or {})
    timeline = result.get("timeline") or {}
    timeline = timeline.get("timeline", timeline)  # live payload nests timeline.timeline
    instructions = timeline.get("instructions") or []

    ids: List[str] = []
    seen = set()
    cursor: Optional[str] = None

    for instruction in instructions:
        if not isinstance(instruction, dict):
            continue
        for entry in _iter_entry_dicts(instruction):
            entry_id = entry.get("entryId") or entry.get("entry_id") or ""
            # Pitfall L3: cursor entryId looks like "cursor-bottom-1234567890"
            if entry_id.startswith("cursor-bottom"):
                value = ((entry.get("content") or {}).get("value")) or entry.get("value")
                if value:
                    cursor = value
                continue
            if entry_id.startswith("cursor-top"):
                continue
            tweet_id = _tweet_id_from_entry(entry)
            if tweet_id and tweet_id not in seen:
                seen.add(tweet_id)
                ids.append(tweet_id)
    return ids, cursor


# --------------------------------------------------------------------------- #
# public iteration API
# --------------------------------------------------------------------------- #
def iter_articles(
    client: XClient,
    handle: str,
    *,
    user_id: Optional[str] = None,
    page_size: int = 20,
    page_delay: float = 0.5,
    max_pages: int = 200,
) -> Iterator[ArticleRef]:
    """Yield every ArticleRef of ``handle``, one page at a time.

    Termination (all four, pitfall L4): no cursor / cursor loop / zero new ids /
    ``max_pages`` safety cap.
    """
    screen_name = _normalize_handle(handle)
    if user_id:
        uid = str(user_id)
    else:
        uid = resolve_user_id(client, handle).user_id

    index = 0
    cursor: Optional[str] = None
    seen_cursors = set()
    seen_tweets = set()
    pages = 0

    while pages < max_pages:
        variables = {
            "userId": uid,
            "count": page_size,
            "includePromotedContent": False,
            "withVoice": True,
        }
        if cursor:
            variables["cursor"] = cursor

        try:
            data = client.graphql(ARTICLES_OP, variables, session=True)  # pitfall L2
        except GraphQLAPIError as exc:
            if pages == 0:
                raise
            logger.warning("paging stopped on page %d: %s", pages + 1, exc)
            break

        ids, next_cursor = _parse_entries({"data": data})
        pages += 1
        new_ids = [tid for tid in ids if tid not in seen_tweets]
        for tweet_id in new_ids:
            seen_tweets.add(tweet_id)
            index += 1
            yield ArticleRef(
                tweet_id=tweet_id,
                url="https://x.com/%s/status/%s" % (screen_name, tweet_id),  # pitfall L6
                sort_index=index - 1,
            )

        logger.debug(
            "page %d: %d ids (%d new), total %d, cursor=%s",
            pages,
            len(ids),
            len(new_ids),
            index,
            (next_cursor or "-")[:24],
        )

        if not next_cursor:
            break
        if next_cursor in seen_cursors:
            logger.debug("cursor loop detected, stopping")
            break
        if not new_ids:
            logger.debug("no new ids on this page, stopping")
            break
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        if page_delay:
            time.sleep(page_delay)  # pitfall L12
    else:
        logger.warning("reached max_pages=%d, stopping", max_pages)


def list_articles(
    client: XClient,
    handle: str,
    *,
    limit: int = 0,
    user_id: Optional[str] = None,
    **kwargs,
) -> List[ArticleRef]:
    """Eager version of :func:`iter_articles`; ``limit`` stops early (pitfall CLI1)."""
    it = iter_articles(client, handle, user_id=user_id, **kwargs)
    if limit and limit > 0:
        return list(itertools.islice(it, limit))
    return list(it)


__all__ = [
    "ArticleRef",
    "UserInfo",
    "iter_articles",
    "list_articles",
    "resolve_user_id",
]
