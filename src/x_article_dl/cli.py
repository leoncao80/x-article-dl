"""Command line interface: ``x-article-dl <handle>``."""
from __future__ import annotations

import argparse
import itertools
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from . import auth, download, listing
from .errors import ConfigError, XadlError

logger = logging.getLogger(__name__)

BEIJING = timezone(timedelta(hours=8))

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_CONFIG = 2
EXIT_ALL_FAILED = 3


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("x-article-dl")
    except Exception:  # 源码目录直接跑
        return "0.1.1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="x-article-dl",
        description="下载某个 X（Twitter）博主的全部 Articles，转成 Markdown（仅走私有 GraphQL API）。",
    )
    parser.add_argument("handle", help="博主 handle，可带 @，如 @eng_khairallah1")
    parser.add_argument(
        "--outdir",
        default=None,
        help="输出目录（默认 ./out/<handle>_articles）",
    )
    parser.add_argument("--limit", type=int, default=0, help="只下最新 N 篇（0=全部）")

    net = parser.add_argument_group("网络")
    net.add_argument("--proxy", default=None, help="代理地址，如 http://127.0.0.1:7897（默认直连；也可用 XADL_PROXY / HTTPS_PROXY）")
    net.add_argument("--no-proxy", action="store_true", help="直连，不使用代理")
    net.add_argument("--timeout", type=float, default=30.0, help="单请求超时秒数（默认 30）")
    net.add_argument("--retries", type=int, default=3, help="失败重试次数（默认 3）")
    net.add_argument("--ua", default=None, help="自定义 User-Agent（默认 Chrome 126）")
    net.add_argument("--no-wait", action="store_true", help="不等待：不限流等待、篇间不 sleep")

    cred = parser.add_argument_group("认证")
    cred.add_argument("--cookies", default=None, help="Netscape cookies 文件（默认自动探测）")
    cred.add_argument("--bearer", default=None, help="手工指定 Bearer（默认从 gallery-dl 源码读取）")
    cred.add_argument("--user-id", default=None, help="直填 userId，跳过 UserByScreenName")

    out = parser.add_argument_group("输出")
    out.add_argument("--force", action="store_true", help="忽略已有 page.json，强制重下")
    out.add_argument("--no-images", action="store_true", help="不下载图片，保留远端 URL")
    out.add_argument("--save-raw", action="store_true", help="保存原始 GraphQL 响应 raw.json（含敏感数据）")
    out.add_argument("--json", action="store_true", help="stdout 输出机器可读汇总")
    out.add_argument("--delay", type=float, default=1.0, help="篇间间隔秒数（默认 1.0）")

    parser.add_argument("-q", "--quiet", action="store_true", help="只打警告和错误")
    parser.add_argument("-v", "--verbose", action="store_true", help="打印调试细节")
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
                "列表接口必须使用登录会话（OAuth2Session），仅 --bearer 不够；"
                "请用 --cookies 指定含 auth_token 与 ct0 的 Netscape cookies 文件。"
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
    """Resolve the user (unless ``--user-id``) and stream article refs (坑 CLI1)."""
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
    now = datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M (+08:00)")
    lines = [
        "# @%s 的 X Articles（%d/%d 篇）" % (handle, ok, total),
        "",
        "- 抓取时间：%s（北京时间）" % now,
        "- 工具：x-article-dl %s" % _version(),
        "- 来源：https://x.com/%s/articles" % handle,
        "",
        "| # | 标题 | 字数 | 图 | 链接 |",
        "|---|------|------|----|------|",
    ]
    for number, article in enumerate(results, start=1):
        title = (article.title or "(untitled)").replace("|", "\\|").strip()
        images = len(article.images) + (1 if article.cover else 0)
        lines.append(
            "| %d | %s | %d | %d | [article.md](%s/article.md) |"
            % (number, title, article.chars, images, article.tweet_id)
        )
    if failures:
        lines += ["", "## 失败清单"]
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
        raise ConfigError("handle 不能为空")

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
            logger.warning("[%d] %s 失败：%s", number, ref.tweet_id, reason)
            failures.append((ref, reason))
            continue

        results.append(article)
        logger.info(
            "[%d] %s %s（%d 字，%d 图，%.1fs）",
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
    logger.info("完成：%d 成功 / %d 失败 → %s", ok, failed, index_path)

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
        logger.error("已中断")
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
