"""Command line interface: ``x-article-dl <handle>``."""
from __future__ import annotations

import argparse
import itertools
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from . import auth, download, listing
from .errors import ConfigError, XadlError

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_CONFIG = 2
EXIT_ALL_FAILED = 3


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("x-article-dl")
    except Exception:  # running from a source checkout
        return "0.1.3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="x-article-dl",
        description="Download all Articles of an X (Twitter) user as Markdown (private GraphQL API only).",
    )
    parser.add_argument("handle", help="user handle, with or without @ (e.g. @eng_khairallah1)")
    parser.add_argument(
        "--outdir",
        default=None,
        help="output directory (default: ./out/<handle>_articles)",
    )
    parser.add_argument("--limit", type=int, default=0, help="only the newest N articles (0 = all)")

    net = parser.add_argument_group("network")
    net.add_argument("--proxy", default=None, help="proxy URL, e.g. http://127.0.0.1:7897 (direct by default; XADL_PROXY / HTTPS_PROXY also work)")
    net.add_argument("--no-proxy", action="store_true", help="force a direct connection")
    net.add_argument("--timeout", type=float, default=30.0, help="per-request timeout in seconds (default: 30)")
    net.add_argument("--retries", type=int, default=3, help="retry attempts for failed requests (default: 3)")
    net.add_argument("--ua", default=None, help="custom User-Agent (default: Chrome 126)")
    net.add_argument("--no-wait", action="store_true", help="do not wait: no rate-limit sleeps and no inter-article delay")

    cred = parser.add_argument_group("authentication")
    cred.add_argument("--cookies", default=None, help="Netscape cookie file (auto-detected by default)")
    cred.add_argument("--bearer", default=None, help="bearer token (default: read from gallery-dl's source)")
    cred.add_argument("--user-id", default=None, help="userId to skip the UserByScreenName lookup")

    out = parser.add_argument_group("output")
    out.add_argument("--force", action="store_true", help="ignore existing page.json and re-download")
    out.add_argument("--no-images", action="store_true", help="keep remote image URLs instead of downloading")
    out.add_argument("--save-raw", action="store_true", help="save the raw GraphQL response as raw.json (contains sensitive data)")
    out.add_argument("--json", action="store_true", help="print a machine-readable summary to stdout")
    out.add_argument("--delay", type=float, default=1.0, help="delay between articles in seconds (default: 1.0)")

    parser.add_argument("-q", "--quiet", action="store_true", help="warnings and errors only")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug details")
    parser.add_argument("--version", action="version", version="x-article-dl " + _version())
    return parser


def _setup_logging(args: argparse.Namespace) -> None:
    level = logging.INFO
    if args.quiet:
        level = logging.WARNING
    if args.verbose:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def _build_client(args: argparse.Namespace):
    proxy = None if args.no_proxy else auth.resolve_proxy(args.proxy)
    try:
        cookies = auth.load_cookies(args.cookies)
    except ConfigError:
        if args.bearer:
            raise ConfigError(
                "the listing endpoint requires an authenticated session (OAuth2Session); "
                "--bearer alone is not enough. Pass a Netscape cookie file containing "
                "auth_token and ct0 via --cookies."
            )
        raise
    bearer = args.bearer or auth.read_bearer()
    user_agent = args.ua or auth.DEFAULT_UA
    creds = auth.Credentials(
        bearer=bearer,
        auth_token=cookies.get("auth_token"),
        ct0=cookies.get("ct0"),
        user_agent=user_agent,
    )
    limits = auth.Limits(
        timeout=args.timeout,
        retries=max(1, args.retries),
        respect_rate_limit=not args.no_wait,
    )
    return auth.XClient(
        proxy=proxy, creds=creds, limits=limits, user_agent=user_agent
    )


def _iter_refs(args, client, handle: str):
    """Resolve the user (unless ``--user-id``) and stream article refs (pitfall CLI1)."""
    if args.user_id:
        user_id: Optional[str] = str(args.user_id)
        screen_name = handle
    else:
        info = listing.resolve_user_id(client, handle)
        user_id = info.user_id
        screen_name = info.screen_name

    refs = listing.iter_articles(
        client,
        handle,
        user_id=user_id,
        page_delay=0.0 if args.no_wait else 0.5,
    )
    if args.limit and args.limit > 0:
        refs = itertools.islice(refs, args.limit)
    return screen_name, refs


def _write_index(
    outdir: Path,
    handle: str,
    results: List[download.Article],
    failures: List[Tuple[listing.ArticleRef, str]],
) -> Path:
    ok = len(results)
    total = ok + len(failures)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    lines = [
        "# X Articles by @%s (%d/%d)" % (handle, ok, total),
        "",
        "- Fetched at: %s (UTC)" % now,
        "- Tool: x-article-dl %s" % _version(),
        "- Source: https://x.com/%s/articles" % handle,
        "",
        "| # | Title | Chars | Images | Link |",
        "|---|-------|-------|--------|------|",
    ]
    for number, article in enumerate(results, start=1):
        title = (article.title or "(untitled)").replace("|", "\\|").strip()
        images = len(article.images) + (1 if article.cover else 0)
        lines.append(
            "| %d | %s | %d | %d | [article.md](%s/article.md) |"
            % (number, title, article.chars, images, article.tweet_id)
        )
    if failures:
        lines += ["", "## Failures"]
        for ref, reason in failures:
            lines.append("- %s — %s（%s）" % (ref.tweet_id, reason, ref.url))
    lines.append("")

    path = outdir / "index.md"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _run(args: argparse.Namespace) -> int:
    handle = (args.handle or "").strip().lstrip("@")
    if not handle:
        raise ConfigError("handle must not be empty")

    outdir = Path(
        args.outdir or (Path("out") / ("%s_articles" % handle))
    ).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)

    client = _build_client(args)

    results: List[download.Article] = []
    failures: List[Tuple[listing.ArticleRef, str]] = []
    delay = 0.0 if args.no_wait else max(0.0, args.delay)
    image_delay = 0.0 if args.no_wait else 0.2

    try:
        screen_name, refs = _iter_refs(args, client, handle)
    except XadlError:
        raise

    total_planned = args.limit if args.limit > 0 else 0
    for number, ref in enumerate(refs, start=1):
        started = time.time()
        try:
            article = download.download_article(
                client,
                ref.tweet_id,
                outdir,
                tweet_url=ref.url,
                force=args.force,
                download_images=not args.no_images,
                save_raw=args.save_raw,
                image_delay=image_delay,
            )
        except XadlError as exc:
            reason = "%s: %s" % (type(exc).__name__, exc)
            logger.warning("[%d] %s failed: %s", number, ref.tweet_id, reason)
            failures.append((ref, reason))
            continue

        results.append(article)
        logger.info(
            "[%d] %s %s (%d chars, %d image(s), %.1fs)",
            number,
            article.tweet_id,
            article.title[:60],
            article.chars,
            len(article.images) + (1 if article.cover else 0),
            time.time() - started,
        )
        if article.warnings:
            for warning in article.warnings:
                logger.debug("  warning: %s", warning)

        if delay and (not total_planned or number < total_planned):
            time.sleep(delay)

    ok = len(results)
    failed = len(failures)
    if ok == 0:
        exit_code = EXIT_ALL_FAILED
    elif failed:
        exit_code = EXIT_PARTIAL
    else:
        exit_code = EXIT_OK

    index_path = _write_index(outdir, screen_name, results, failures)
    logger.info("done: %d succeeded / %d failed -> %s", ok, failed, index_path)

    if args.json:
        print(
            json.dumps(
                {
                    "handle": screen_name,
                    "ok": ok,
                    "failed": failed,
                    "exit_code": exit_code,
                    "outdir": str(outdir),
                    "index": str(index_path),
                    "articles": [
                        {
                            "tweet_id": a.tweet_id,
                            "title": a.title,
                            "chars": a.chars,
                            "images": len(a.images) + (1 if a.cover else 0),
                            "url": a.tweet_url,
                        }
                        for a in results
                    ],
                    "failures": [
                        {"tweet_id": ref.tweet_id, "url": ref.url, "reason": reason}
                        for ref, reason in failures
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return exit_code


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args)
    try:
        return _run(args)
    except XadlError as exc:
        logger.error("%s", exc)
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {"ok": 0, "error": str(exc), "exit_code": exc.exit_code},
                    ensure_ascii=False,
                )
            )
        return exc.exit_code
    except KeyboardInterrupt:
        logger.error("interrupted")
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
