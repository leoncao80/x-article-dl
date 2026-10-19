"""X GraphQL endpoint constants (private API).

All queryIds live here so a rotation only needs a single edit (pitfalls L1,
D2).  Nothing in this module performs I/O or reads secrets.
"""
from __future__ import annotations

import json
import urllib.parse
from typing import Dict, Mapping, Optional

GRAPHQL_BASE = "https://x.com/i/api/graphql"
GUEST_ACTIVATE_URL = "https://api.x.com/1.1/guest/activate.json"

# Query IDs captured from the web client on 2026-10-19. When they rotate, this
# table is the only place to edit.
QUERY_IDS: Dict[str, str] = {
    # handle -> userId / screen_name
    "UserByScreenName": "ck5KkZ8t5cOmoLssopN99Q",
    # The author's Articles timeline (a tab gallery-dl does not implement;
    # pitfall L1).
    "UserArticlesTweets": "G9ehBUadUrsNfz-XnAcV4A",
    # Single tweet; combined with FIELD_TOGGLES this returns
    # article.content_state (pitfall D2).
    # Do not confuse it with TweetDetail's 2ICDjqPd81tulZcYrtpTuQ.
    "TweetResultByRestId": "qxWQxcMLiTPcavz9Qy5hwQ",
}

# Minimum feature set per operation (pitfalls L11 / D3).
FEATURES: Dict[str, Dict[str, bool]] = {
    "UserByScreenName": {},
    "UserArticlesTweets": {
        "articles_preview_enabled": True,
    },
    "TweetResultByRestId": {
        "articles_preview_enabled": True,
        # Without these two the article field is stripped or GraphQL returns 400
        "responsive_web_twitter_article_tweet_consumption_enabled": True,
        "longform_notetweets_consumption_enabled": True,
    },
}

# fieldToggles only matter for TweetResultByRestId.
# withArticleRichContentState=true is the only switch that returns the full body
# (Draft.js blocks, pitfall D1).
# Keep withArticlePlainText false: enabling it degrades the body to plain text and
# loses all structure.
FIELD_TOGGLES: Dict[str, Dict[str, bool]] = {
    "TweetResultByRestId": {
        "withArticleRichContentState": True,
        "withArticlePlainText": False,
        "withGrokAnalyze": False,
        "withDisallowedReplyControls": False,
    },
}


def _quote(obj: Mapping) -> str:
    """Serialize a dict the way the web client does (compact JSON, fully quoted)."""
    return urllib.parse.quote(json.dumps(obj, separators=(",", ":")), safe="")


def build_graphql_url(
    op: str,
    variables: Mapping,
    features: Optional[Mapping] = None,
    field_toggles: Optional[Mapping] = None,
    qid: Optional[str] = None,
) -> str:
    """Build ``/i/api/graphql/<qid>/<op>?variables=...&features=...&fieldToggles=...``.

    ``features`` / ``field_toggles`` default to the per-operation tables above.
    Raises KeyError for an unknown operation name.
    """
    query_id = qid or QUERY_IDS[op]
    feats = dict(FEATURES.get(op, {}) if features is None else features)
    url = "%s/%s/%s?variables=%s" % (GRAPHQL_BASE, query_id, op, _quote(variables))
    if feats:
        url += "&features=" + _quote(feats)
    toggles = FIELD_TOGGLES.get(op, {}) if field_toggles is None else field_toggles
    if toggles:
        url += "&fieldToggles=" + _quote(toggles)
    return url


__all__ = [
    "GRAPHQL_BASE",
    "GUEST_ACTIVATE_URL",
    "QUERY_IDS",
    "FEATURES",
    "FIELD_TOGGLES",
    "build_graphql_url",
]
