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

# queryId 抓包时间：2026-10-19（网页版）。轮换时只改这一张表。
QUERY_IDS: Dict[str, str] = {
    # 取 userId / screen_name
    "UserByScreenName": "ck5KkZ8t5cOmoLssopN99Q",
    # 博主 Articles 时间线（这是 gallery-dl 没有的 tab，必须自己调；坑 L1）
    "UserArticlesTweets": "G9ehBUadUrsNfz-XnAcV4A",
    # 单条推文；配合 FIELD_TOGGLES 拿 article.content_state（坑 D2）
    # 注意：不要与 TweetDetail 的 2ICDjqPd81tulZcYrtpTuQ 混用。
    "TweetResultByRestId": "qxWQxcMLiTPcavz9Qy5hwQ",
}

# 每个 operation 的最小可用 feature 集（坑 L11 / D3）。
FEATURES: Dict[str, Dict[str, bool]] = {
    "UserByScreenName": {},
    "UserArticlesTweets": {
        "articles_preview_enabled": True,
    },
    "TweetResultByRestId": {
        "articles_preview_enabled": True,
        # 缺这两项时 article 字段会被裁掉 / GraphQL 400
        "responsive_web_twitter_article_tweet_consumption_enabled": True,
        "longform_notetweets_consumption_enabled": True,
    },
}

# fieldToggles 只对 TweetResultByRestId 有意义。
# withArticleRichContentState=true 是拿到全篇正文（Draft.js blocks）的唯一开关（坑 D1）；
# withArticlePlainText 保持 False，否则正文退化成纯文本、丢失结构。
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
