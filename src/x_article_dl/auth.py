"""Authentication, cookies and the HTTP transport for x-article-dl.

Design notes (see docs, pitfalls A1-A10):

* the bearer token is read **live** from gallery-dl's ``twitter.py`` source, so a
  token rotation upstream is picked up automatically;
* ``auth_token`` / ``ct0`` come from a Netscape cookie file exported from the
  browser (``#HttpOnly_`` prefixed lines included -- ``auth_token`` is HttpOnly);
* every request goes through a privately built opener so that environment proxy
  variables can never leak in;
* library code never calls ``sys.exit()`` and never logs a token.
"""
from __future__ import annotations

import glob
import importlib.metadata as importlib_metadata
import json
import logging
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple

from .errors import AuthError, ConfigError, GraphQLAPIError, NetworkError, RateLimitError

logger = logging.getLogger(__name__)

DEFAULT_PROXY = "http://127.0.0.1:7897"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
COOKIE_ENV = "XADL_COOKIES"
PROXY_ENV = "XADL_PROXY"
SSL_NO_VERIFY_ENV = "XADL_SSL_NO_VERIFY"
# 只接受这些 cookie 域（精确匹配或子域，坑 A2/A4）
X_DOMAINS = ("x.com", "twitter.com")

GALLERY_DL_CANDIDATES = (
    "~/.local/lib/python*/site-packages/gallery_dl/extractor/twitter.py",
    "~/.local/pipx/venvs/gallery-dl/lib/python*/site-packages/gallery_dl/extractor/twitter.py",
    "/usr/lib/python3*/site-packages/gallery_dl/extractor/twitter.py",
    "/usr/lib/python3/dist-packages/gallery_dl/extractor/twitter.py",
    "/usr/local/lib/python3*/site-packages/gallery_dl/extractor/twitter.py",
    "/usr/local/lib/python3*/dist-packages/gallery_dl/extractor/twitter.py",
)

# gallery-dl 把 Bearer 写成相邻多段字符串拼接：
#     "authorization": (
#         "AAAAAAAA..."
#         "1Zv7ttfk..."
#     ),
# 只抓第一段会得到被截断的 token，所有请求 401（坑 A1）。
_BEARER_RE = re.compile(r'"authorization"\s*:\s*\(?\s*((?:\s*"[^"]*"\s*)+)')
_QUOTED_RE = re.compile(r'"([^"]*)"')
# gallery-dl 写的是 "/graphql/<qid>/<op>"（无 /i/api 前缀），两种形态都兼容。
_QUERY_ID_RE = r'/graphql/([A-Za-z0-9_-]+)/{}"'


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def mask(token: Optional[str]) -> str:
    """Return a log-safe rendering of a secret."""
    if not token:
        return "<none>"
    if len(token) <= 8:
        return "***"
    return "%s...%s(len=%d)" % (token[:4], token[-4:], len(token))


def resolve_proxy(cli_proxy: Optional[str] = None, *, no_proxy: bool = False) -> Optional[str]:
    """Resolve the proxy URL: ``--proxy`` > ``XADL_PROXY`` > ``HTTPS_PROXY`` > default.

    Returns ``None`` when the caller asked for a direct connection (坑 A8).
    """
    if no_proxy:
        return None
    if cli_proxy:
        return cli_proxy
    for key in (PROXY_ENV, "HTTPS_PROXY", "https_proxy"):
        value = os.environ.get(key)
        if value:
            return value
    return DEFAULT_PROXY


@dataclass(frozen=True)
class Credentials:
    """Bearer + session cookies needed for the private GraphQL API."""

    bearer: str = field(repr=False)
    auth_token: Optional[str] = field(default=None, repr=False)
    ct0: Optional[str] = field(default=None, repr=False)
    user_agent: str = DEFAULT_UA

    @property
    def has_session(self) -> bool:
        return bool(self.auth_token and self.ct0)

    def __repr__(self) -> str:  # never leak the token via logs / tracebacks
        return "Credentials(bearer=%s, auth_token=%s, ct0=%s)" % (
            mask(self.bearer),
            mask(self.auth_token),
            mask(self.ct0),
        )


@dataclass
class Limits:
    timeout: float = 30.0
    retries: int = 3
    backoff: float = 2.0
    respect_rate_limit: bool = True


# --------------------------------------------------------------------------- #
# gallery-dl source introspection (bearer + query ids)
# --------------------------------------------------------------------------- #
def locate_gallery_dl_twitter(path=None) -> Path:
    """Find gallery-dl's ``extractor/twitter.py``.

    Order: explicit path -> the already imported package -> glob candidates
    (newest mtime first) -> importlib.metadata (坑 A5).
    """
    if path:
        candidate = Path(path).expanduser()
        if not candidate.is_file():
            raise ConfigError("指定的 gallery-dl twitter.py 不存在：%s" % candidate)
        return candidate

    try:  # if gallery-dl is importable we know exactly where it lives
        import gallery_dl  # type: ignore

        candidate = Path(gallery_dl.__file__).resolve().parent / "extractor" / "twitter.py"
        if candidate.is_file():
            return candidate
    except Exception:  # pragma: no cover - gallery-dl is an optional neighbour
        pass

    found = []
    for pattern in GALLERY_DL_CANDIDATES:
        found.extend(p for p in glob.glob(os.path.expanduser(pattern)) if os.path.isfile(p))
    if not found:
        try:
            dist = importlib_metadata.distribution("gallery-dl")
            root = Path(str(dist.locate_file("gallery_dl"))) / "extractor" / "twitter.py"
            if root.is_file():
                found.append(str(root))
        except Exception:
            pass
    if not found:
        raise ConfigError(
            "未找到 gallery-dl 的 twitter.py（Bearer 来源）。请 pip install gallery-dl，"
            "或用 --bearer 显式传入 Bearer token。"
        )
    # 多命中时取 mtime 最新的一份，避免读到旧版本（坑 A5）
    found.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return Path(found[0])


def _read_source(path=None) -> Tuple[Path, str]:
    src_path = locate_gallery_dl_twitter(path)
    try:
        return src_path, src_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ConfigError("读取失败 %s：%s" % (src_path, exc)) from exc


def read_bearer(path=None, *, min_len: int = 50) -> str:
    """Extract the bearer token from gallery-dl's source (all segments joined)."""
    src_path, src = _read_source(path)
    match = _BEARER_RE.search(src)
    if not match:
        raise ConfigError("在 %s 里没找到 Bearer token（gallery-dl 可能改了写法）" % src_path)
    token = "".join(_QUOTED_RE.findall(match.group(1)))
    if token.lower().startswith("bearer "):
        token = token[len("bearer ") :]
    token = token.strip()
    # 注意：不要 unquote %3D —— X 期望的就是百分号编码后的形态。
    if len(token) < min_len:
        raise ConfigError(
            "读到的 Bearer 疑似不完整（长度 %d < %d）" % (len(token), min_len)
        )
    logger.debug("bearer loaded from %s (%s)", src_path, mask(token))
    return token


def read_query_id(name: str, path=None) -> Optional[str]:
    """Read ``/graphql/<QID>/<name>`` from gallery-dl; ``None`` when absent."""
    try:
        _, src = _read_source(path)
    except ConfigError:
        return None
    match = re.search(_QUERY_ID_RE.format(re.escape(name)), src)
    return match.group(1) if match else None


# --------------------------------------------------------------------------- #
# cookies
# --------------------------------------------------------------------------- #
def default_cookie_path() -> Path:
    """``XADL_COOKIES`` -> ``./x_cookies.txt`` -> ``~/.config/x-article-dl/cookies.txt``."""
    candidates = []
    env = os.environ.get(COOKIE_ENV)
    if env:
        # 显式指定就不静默回退 —— 否则报错信息指向的是另一个路径（坑 A4）
        return Path(env).expanduser()
    candidates.append(Path.cwd() / "x_cookies.txt")
    candidates.append(Path("~/.config/x-article-dl/cookies.txt").expanduser())
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[-1]


def _is_x_domain(domain: str) -> bool:
    """True for x.com / twitter.com and any of their subdomains.

    不能用 `"x.com" in domain`：netflix.com 也包含子串 "x.com"。
    """
    d = domain.lower().lstrip(".")
    return any(d == base or d.endswith("." + base) for base in X_DOMAINS)


def load_cookies(path=None) -> Dict[str, str]:
    """Parse a Netscape cookie file into ``{name: value}``.

    ``#HttpOnly_`` lines must survive the comment filter: ``auth_token`` is
    HttpOnly, and dropping it means every authenticated call returns 401 (坑 A2).
    """
    cookie_path = Path(path).expanduser() if path else default_cookie_path()
    if not cookie_path.is_file():
        raise ConfigError(
            "找不到 cookie 文件 %s。请从浏览器导出 Netscape 格式的 cookies（需含 "
            "auth_token 与 ct0），或用 --cookies 指定路径。" % cookie_path
        )

    cookies: Dict[str, str] = {}
    now = time.time()
    try:
        lines = cookie_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise ConfigError("读取 cookie 文件失败 %s：%s" % (cookie_path, exc)) from exc

    for raw in lines:
        line = raw.strip("\r\n")
        if not line.strip():
            continue
        # 坑 A2：先剥掉 #HttpOnly_ 前缀，再判断是否注释行
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_") :]
        elif line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, _path, _secure, expires, name, value = parts[:7]
        if domain and not _is_x_domain(domain):
            continue
        try:
            if expires and int(expires) > 0 and int(expires) < now:
                continue
        except ValueError:
            pass
        cookies[name] = value

    missing = [n for n in ("auth_token", "ct0") if not cookies.get(n)]
    if missing:
        raise ConfigError(
            "cookie 文件 %s 缺少 %s（需要已登录的 X 会话）"
            % (cookie_path, " / ".join(missing))
        )
    ct0 = cookies.get("ct0", "")
    if ct0 and len(ct0) != 160:  # 160 is the usual length; warn but continue（坑 A4）
        logger.warning("ct0 长度异常（%d），可能不是完整的会话 cookie", len(ct0))
    logger.debug("cookies loaded from %s: %s", cookie_path, sorted(cookies))
    return cookies


def build_headers(
    creds: Credentials, *, session: bool = True, guest_token: Optional[str] = None
) -> Dict[str, str]:
    """Build request headers for a session-authenticated or guest call."""
    headers = {
        "authorization": "Bearer " + creds.bearer,
        "User-Agent": creds.user_agent or DEFAULT_UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if not session:
        if guest_token:
            headers["x-guest-token"] = guest_token
        return headers

    if not creds.has_session:
        raise AuthError("缺少 auth_token/ct0，无法进行会话认证")
    headers.update(
        {
            # 坑 A3：缺这个头 UserArticlesTweets 直接 401
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "en",
            "x-csrf-token": creds.ct0 or "",
            # 坑 A4：ct0 必须同时出现在 header 与 cookie 里，且同属一个会话
            "Cookie": "auth_token=%s; ct0=%s" % (creds.auth_token, creds.ct0),
        }
    )
    return headers


# --------------------------------------------------------------------------- #
# transport
# --------------------------------------------------------------------------- #
def _build_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if os.environ.get(SSL_NO_VERIFY_ENV, "").lower() in ("1", "true", "yes"):
        logger.warning("%s 已启用：跳过 TLS 证书校验（仅用于自签代理）", SSL_NO_VERIFY_ENV)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


class XClient:
    """Small HTTP client for X's private GraphQL API.

    One opener for the whole run (坑 L10): ``ProxyHandler`` is always explicit so
    environment proxy variables cannot hijack the connection (坑 A9).
    """

    def __init__(
        self,
        *,
        proxy: Optional[str] = DEFAULT_PROXY,
        creds: Optional[Credentials] = None,
        limits: Optional[Limits] = None,
        user_agent: str = DEFAULT_UA,
        bearer_path=None,
    ) -> None:
        self.limits = limits or Limits()
        self.user_agent = user_agent
        self.bearer_path = bearer_path
        if proxy:
            logger.debug("using proxy %s", proxy)
            proxy_handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        else:
            # 显式禁用环境变量里的代理（坑 A9）
            proxy_handler = urllib.request.ProxyHandler({})
        self._opener = urllib.request.build_opener(
            proxy_handler, urllib.request.HTTPSHandler(context=_build_ssl_context())
        )
        self._creds = creds
        self._bearer_cache: Optional[Tuple[str, float, int, str]] = None
        self._guest_token: Optional[str] = None

    # -- credentials ------------------------------------------------------- #
    def credentials(self, *, refresh: bool = False) -> Credentials:
        """Return credentials, re-reading the bearer when gallery-dl changed."""
        if self._creds is None or refresh:
            bearer = read_bearer(self.bearer_path)
            self._creds = Credentials(
                bearer=bearer,
                auth_token=getattr(self._creds, "auth_token", None),
                ct0=getattr(self._creds, "ct0", None),
                user_agent=self.user_agent,
            )
        else:
            bearer = self._creds.bearer
            try:  # mtime cache: pick up a gallery-dl upgrade without restarting
                src_path = locate_gallery_dl_twitter(self.bearer_path)
                stat = src_path.stat()
                key = (str(src_path), stat.st_mtime, stat.st_size, bearer)
                if self._bearer_cache is not None and self._bearer_cache[:3] != key[:3]:
                    logger.info("gallery-dl 源码已更新，重新读取 Bearer")
                    return self.credentials(refresh=True)
                self._bearer_cache = key
            except (ConfigError, OSError):
                pass
        if not self._creds.user_agent:
            self._creds = Credentials(
                bearer=self._creds.bearer,
                auth_token=self._creds.auth_token,
                ct0=self._creds.ct0,
                user_agent=self.user_agent,
            )
        return self._creds

    # -- guest token -------------------------------------------------------- #
    def guest_token(self, *, refresh: bool = False) -> str:
        """Fetch / cache a guest token (IP scoped, ~3h lifetime; 坑 A7)."""
        if self._guest_token and not refresh:
            return self._guest_token
        creds = self.credentials()
        req = urllib.request.Request(
            "https://api.x.com/1.1/guest/activate.json",
            data=b"",  # 必须空体 POST，且绝不带 cookie
            method="POST",
            headers={
                "authorization": "Bearer " + creds.bearer,
                "User-Agent": creds.user_agent or DEFAULT_UA,
                "Content-Length": "0",
            },
        )
        try:
            with self._opener.open(req, timeout=self.limits.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc, session=False) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise NetworkError("获取 guest token 失败：%s" % exc) from exc
        token = (payload or {}).get("guest_token")
        if not token:
            raise NetworkError("guest/activate.json 未返回 guest_token")
        self._guest_token = token
        logger.debug("guest token acquired (%s)", mask(token))
        return token

    # -- low level ---------------------------------------------------------- #
    @staticmethod
    def _http_error(exc: urllib.error.HTTPError, *, session: bool) -> Exception:
        code = exc.code
        try:
            body = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            body = ""
        if code in (401, 403):
            hint = ""
            if code == 403 and session:
                hint = "（多为 ct0 不匹配或会话过期）"
            return AuthError("HTTP %d%s %s" % (code, hint, body.strip()))
        if code == 429:
            reset_at = None
            raw = exc.headers.get("x-rate-limit-reset") if exc.headers else None
            try:
                reset_at = float(raw) if raw else None
            except (TypeError, ValueError):
                reset_at = None
            return RateLimitError("HTTP 429 rate limited", reset_at=reset_at)
        if 500 <= code < 600:
            return NetworkError("HTTP %d %s" % (code, body.strip()))
        return NetworkError("HTTP %d %s" % (code, body.strip()))

    def _open_once(self, req: urllib.request.Request, *, session: bool) -> bytes:
        try:
            with self._opener.open(req, timeout=self.limits.timeout) as resp:
                self._last_content_type = resp.headers.get("Content-Type")
                return resp.read()
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc, session=session) from exc
        except (urllib.error.URLError, OSError, ssl.SSLError) as exc:
            raise NetworkError("网络请求失败：%s" % exc) from exc

    def _sleep_for(self, exc: Exception, attempt: int) -> None:
        delay = self.limits.backoff ** attempt
        if isinstance(exc, RateLimitError) and exc.reset_at and self.limits.respect_rate_limit:
            wait = exc.reset_at - time.time()
            if wait > 0:
                delay = min(wait + 1.0, 60.0)
        logger.warning("请求失败，%.1fs 后重试（%s）", delay, exc)
        time.sleep(delay)

    def _fetch(self, req: urllib.request.Request, *, session: bool, guest: bool) -> bytes:
        """Run a request with retries, bearer refresh and guest-token refresh."""
        bearer_retried = False
        guest_retried = False
        attempts = max(1, self.limits.retries)
        last_exc: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                return self._open_once(req, session=session)
            except AuthError:
                if guest and not guest_retried:
                    guest_retried = True
                    logger.info("guest token 被拒，刷新后重试")
                    token = self.guest_token(refresh=True)
                    req.add_header("x-guest-token", token)
                    continue
                if session and not bearer_retried:
                    bearer_retried = True
                    logger.info("会话认证失败，重新读取 Bearer 后重试一次")
                    self.credentials(refresh=True)
                    for key in ("authorization", "x-csrf-token", "Cookie"):
                        req.remove_header(key)
                    for key, value in build_headers(self.credentials(), session=True).items():
                        req.add_header(key, value)
                    continue
                raise
            except (RateLimitError, NetworkError) as exc:
                last_exc = exc
                if attempt + 1 >= attempts:
                    break
                self._sleep_for(exc, attempt)
        assert last_exc is not None
        raise last_exc

    # -- public API --------------------------------------------------------- #
    def get_json(self, url: str, *, session: bool = True, guest: bool = False) -> dict:
        creds = self.credentials()
        headers = build_headers(
            creds, session=session, guest_token=self.guest_token() if guest else None
        )
        req = urllib.request.Request(url, headers=headers)
        body = self._fetch(req, session=session, guest=guest)
        try:
            return json.loads(body.decode("utf-8", "replace"))
        except ValueError as exc:
            raise NetworkError("响应不是合法 JSON：%s" % body[:200]) from exc

    def get_bytes_with_type(
        self, url: str, *, referer: Optional[str] = None
    ) -> Tuple[bytes, Optional[str]]:
        """Download raw bytes and report the server's Content-Type (坑 D9)."""
        headers = {"User-Agent": self.user_agent or DEFAULT_UA}
        if referer:
            headers["Referer"] = referer
        req = urllib.request.Request(url, headers=headers)
        self._last_content_type = None
        data = self._fetch(req, session=False, guest=False)
        return data, self._last_content_type

    def get_bytes(self, url: str, *, referer: Optional[str] = None) -> bytes:
        return self.get_bytes_with_type(url, referer=referer)[0]

    def graphql(
        self,
        op: str,
        variables: dict,
        *,
        features: Optional[dict] = None,
        field_toggles: Optional[dict] = None,
        session: bool = True,
        guest: bool = False,
    ) -> dict:
        """Call a GraphQL operation and unwrap the ``errors`` array."""
        from . import _endpoints  # 延迟导入，避免与 __init__ 的再导出循环

        url = _endpoints.build_graphql_url(op, variables, features, field_toggles)
        payload = self.get_json(url, session=session, guest=guest)
        errors = payload.get("errors") or []
        if errors:
            message = errors[0].get("message", "unknown GraphQL error")
            raise GraphQLAPIError("%s: %s" % (op, message), errors)
        if "data" not in payload:
            raise GraphQLAPIError("%s: 响应缺少 data 字段" % op, errors)
        return payload["data"]

    def __repr__(self) -> str:
        return "XClient(creds=%r)" % (self._creds,)


__all__ = [
    "DEFAULT_PROXY",
    "DEFAULT_UA",
    "Credentials",
    "Limits",
    "XClient",
    "build_headers",
    "default_cookie_path",
    "load_cookies",
    "locate_gallery_dl_twitter",
    "mask",
    "read_bearer",
    "read_query_id",
    "resolve_proxy",
]
