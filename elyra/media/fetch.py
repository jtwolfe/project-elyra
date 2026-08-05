"""SSRF-aware HTTPS URL fetch into MediaStore (KD-V18 / view_media).

Host-only. Used by ``view_media`` (and hermetic tests). No cookies, no
forwarded operator secrets, no arbitrary model-supplied headers.

Security controls (normative):
- HTTPS only
- DNS resolve + block non-global IPs (private / CGNAT / link-local / metadata / …)
- **IP-pin connect**: TCP to allowlisted IP; ``Host`` + TLS SNI = original hostname
  (closes DNS rebinding TOCTOU on the default path)
- Redirect revalidation (scheme + IP) with max hops
- Connect+read timeout
- Streamed size budget (URL max then per-kind cap after sniff)
"""

from __future__ import annotations

import hashlib
import http.client
import io
import ipaddress
import logging
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from elyra.config import ElyraPaths, resolve_paths
from elyra.media.store import MediaStore, safe_filename, sniff_mime_and_kind
from elyra.media.types import Attachment
from elyra.media.upload import MAX_FILE_BYTES, max_bytes_for_kind

_LOG = logging.getLogger(__name__)

# Design: URL download budget aligned with largest media kind (48 MiB).
URL_MAX_BYTES = MAX_FILE_BYTES
DEFAULT_TIMEOUT_S = 20.0
MAX_REDIRECTS = 3
_STREAM_CHUNK = 64 * 1024

# Soft User-Agent; no auth / cookies.
_USER_AGENT = "elyra-media-fetch/1"

# Content-Disposition filename*= / filename=
_CD_FILENAME_RE = re.compile(
    r"filename\*\s*=\s*(?:UTF-8''|utf-8'')([^;]+)|filename\s*=\s*\"([^\"]+)\"|filename\s*=\s*([^;]+)",
    re.IGNORECASE,
)

UrlOpenFn = Callable[..., Any]
GetAddrInfoFn = Callable[..., list[tuple[Any, ...]]]


class FetchError(Exception):
    """Structured URL fetch failure; ``reason`` is a stable tool error code."""

    def __init__(self, reason: str, *, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason)


@dataclass(frozen=True)
class FetchedBytes:
    """Raw download result before MediaStore put (sha computed by caller)."""

    data: bytes
    filename: str
    claimed_mime: str | None
    final_url: str


def redacted_url_for_log(url: str) -> str:
    """Scheme+host+path only — never query/fragment (may hold secrets)."""
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:  # noqa: BLE001
        return "<unparseable>"
    host = parts.hostname or ""
    if parts.port:
        netloc = f"{host}:{parts.port}"
    else:
        netloc = host or parts.netloc
    path = parts.path or ""
    return f"{parts.scheme}://{netloc}{path}"


def redacted_source_url(url: str) -> str:
    """Audit-safe source URL (no query/fragment) for promote meta."""
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:  # noqa: BLE001
        return ""
    if parts.scheme != "https" or not parts.hostname:
        return redacted_url_for_log(url)
    netloc = parts.hostname
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urllib.parse.urlunsplit(("https", netloc, parts.path or "", "", ""))


def is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if IP must not be contacted (SSRF private/metadata surface).

    After IPv4-mapped unwrap, any **non-global** address is blocked. That covers
    RFC1918, loopback, link-local (incl. ``169.254.169.254``), multicast,
    unspecified, reserved/documentation, **CGNAT ``100.64.0.0/10``** (incl.
    Alibaba metadata ``100.100.100.200``), and IPv6 ULA / unique-local.
    """
    # Unwrap IPv4-mapped IPv6 so ::ffff:127.0.0.1 is blocked as loopback.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    # is_global is False for CGNAT 100.64/10, private, link-local, etc.
    if not ip.is_global:
        return True
    # Defense in depth for known cloud metadata unicast (if ever classified global).
    if ip == ipaddress.ip_address("169.254.169.254"):
        return True
    if ip == ipaddress.ip_address("fd00:ec2::254"):
        return True
    return False


def _parse_https_url(url: str) -> urllib.parse.ParseResult:
    if not isinstance(url, str) or not url.strip():
        raise FetchError("url_invalid", detail="url must be a non-empty string")
    text = url.strip()
    try:
        parsed = urllib.parse.urlparse(text)
    except Exception as exc:  # noqa: BLE001
        raise FetchError("url_invalid", detail="url parse failed") from exc
    if parsed.scheme.lower() != "https":
        raise FetchError(
            "url_invalid",
            detail="only https URLs are allowed",
        )
    if not parsed.hostname:
        raise FetchError("url_invalid", detail="url missing host")
    # Reject embedded credentials (avoid log/leak vectors).
    if parsed.username is not None or parsed.password is not None:
        raise FetchError("url_invalid", detail="url must not embed credentials")
    return parsed


def _resolve_host_ips(
    hostname: str,
    port: int,
    *,
    getaddrinfo: GetAddrInfoFn | None = None,
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolve hostname; return unique allowlisted IPs. Raises FetchError."""
    # Literal IP in host — no DNS, still block non-global.
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if is_blocked_ip(literal):
            raise FetchError(
                "url_ssrf_blocked",
                detail="target IP is private or otherwise blocked",
            )
        return [literal]

    resolver = getaddrinfo or socket.getaddrinfo
    try:
        infos = resolver(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(
            "url_fetch_failed",
            detail=f"dns_failed:{exc}",
        ) from exc
    except OSError as exc:
        raise FetchError(
            "url_fetch_failed",
            detail=f"dns_os_error:{type(exc).__name__}",
        ) from exc

    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    seen: set[str] = set()
    for info in infos or ():
        # getaddrinfo → (family, type, proto, canonname, sockaddr)
        if len(info) < 5:
            continue
        sockaddr = info[4]
        if not sockaddr:
            continue
        addr = sockaddr[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        key = str(ip)
        if key in seen:
            continue
        seen.add(key)
        if is_blocked_ip(ip):
            raise FetchError(
                "url_ssrf_blocked",
                detail="resolved IP is private or otherwise blocked",
            )
        ips.append(ip)
    if not ips:
        raise FetchError("url_fetch_failed", detail="dns_empty")
    return ips


def _validate_url_target(
    url: str,
    *,
    getaddrinfo: GetAddrInfoFn | None = None,
) -> tuple[urllib.parse.ParseResult, list[ipaddress.IPv4Address | ipaddress.IPv6Address]]:
    """Parse + HTTPS check + DNS/IP block. Returns (parsed, allowlisted IPs)."""
    parsed = _parse_https_url(url)
    port = parsed.port or 443
    assert parsed.hostname is not None
    ips = _resolve_host_ips(parsed.hostname, port, getaddrinfo=getaddrinfo)
    return parsed, ips


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Do not auto-follow; caller revalidates each Location hop."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Mapping[str, str],
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


def _default_urlopen_unpinned(req: urllib.request.Request, timeout: float) -> Any:
    """Legacy opener (hostname re-resolve). Used only if pin path is bypassed."""
    opener = urllib.request.build_opener(_NoRedirectHandler())
    return opener.open(req, timeout=timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that dials ``connect_to`` IP while SNI/Host use ``host``.

    Closes DNS rebinding TOCTOU: TCP never re-resolves the original hostname.
    Certificate validation still uses the original hostname as ``server_hostname``.
    """

    def __init__(
        self,
        host: str,
        port: int | None = None,
        *args: Any,
        connect_to: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._connect_to = connect_to
        super().__init__(host, port, *args, **kwargs)

    def connect(self) -> None:  # type: ignore[override]
        if not self._connect_to:
            super().connect()
            return
        timeout = self.timeout
        sock = socket.create_connection(
            (self._connect_to, self.port),
            timeout,
            self.source_address,
        )
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
            return
        context = self._context
        if context is None:
            context = ssl.create_default_context()
        # SNI + cert hostname check use original host (not the dial IP).
        self.sock = context.wrap_socket(sock, server_hostname=self.host)


class _PinnedResponse:
    """Thin adapter so pinned http.client responses match urlopen responses."""

    def __init__(self, resp: http.client.HTTPResponse, conn: http.client.HTTPSConnection) -> None:
        self._resp = resp
        self._conn = conn
        self.status = int(resp.status)
        self.headers = resp.headers

    def getcode(self) -> int:
        return self.status

    def read(self, n: int = -1) -> bytes:
        return self._resp.read(n)

    def close(self) -> None:
        try:
            self._resp.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass


def _request_path(parsed: urllib.parse.ParseResult) -> str:
    path = parsed.path or "/"
    if parsed.query:
        return f"{path}?{parsed.query}"
    return path


def _close_http_error(exc: urllib.error.HTTPError) -> None:
    try:
        exc.close()
    except Exception:  # noqa: BLE001
        pass


def _pinned_https_open(
    url: str,
    *,
    connect_ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    timeout: float,
    headers: Mapping[str, str],
) -> Any:
    """GET ``url`` over TLS dialed to ``connect_ip``; Host/SNI = original hostname.

    Mirrors urllib ``urlopen`` error behavior for status codes: raises
    ``HTTPError`` for redirects and 4xx/5xx so the caller loop is unified.
    """
    parsed = _parse_https_url(url)
    hostname = parsed.hostname
    assert hostname is not None
    port = parsed.port or 443
    path = _request_path(parsed)
    context = ssl.create_default_context()
    # IPv6: socket.create_connection wants bare address string (no brackets).
    dial = str(connect_ip)
    conn = _PinnedHTTPSConnection(
        hostname,
        port,
        connect_to=dial,
        timeout=timeout,
        context=context,
    )
    try:
        conn.request("GET", path, headers=dict(headers))
        resp = conn.getresponse()
    except TimeoutError:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        raise
    except OSError as exc:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        # Map to URLError-like for fetch_url_bytes handler.
        raise urllib.error.URLError(exc) from exc

    code = int(resp.status)
    if _is_redirect_status(code) or code >= 400:
        # Drain small error/redirect body then raise HTTPError (caller closes).
        try:
            body = resp.read(64 * 1024)
        except Exception:  # noqa: BLE001
            body = b""
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        fp = io.BytesIO(body)
        raise urllib.error.HTTPError(
            url,
            code,
            getattr(resp, "reason", "") or "",
            hdrs=resp.headers,  # type: ignore[arg-type]
            fp=fp,
        )
    return _PinnedResponse(resp, conn)


def _join_redirect(base_url: str, location: str) -> str:
    if not location or not str(location).strip():
        raise FetchError("url_redirect_blocked", detail="empty redirect Location")
    return urllib.parse.urljoin(base_url, str(location).strip())


def _filename_from_url(url: str) -> str:
    path = urllib.parse.urlparse(url).path or ""
    name = path.rsplit("/", 1)[-1] if path else ""
    name = urllib.parse.unquote(name)
    return safe_filename(name or "download")


def _filename_from_content_disposition(header: str | None) -> str | None:
    if not header:
        return None
    m = _CD_FILENAME_RE.search(header)
    if not m:
        return None
    raw = m.group(1) or m.group(2) or m.group(3) or ""
    raw = raw.strip().strip('"').strip()
    if not raw:
        return None
    try:
        raw = urllib.parse.unquote(raw)
    except Exception:  # noqa: BLE001
        pass
    return safe_filename(raw)


def _content_type_mime(header: str | None) -> str | None:
    if not header:
        return None
    # "image/png; charset=binary" → image/png
    return header.split(";", 1)[0].strip().lower() or None


def _read_body_limited(resp: Any, max_bytes: int) -> bytes:
    """Stream response body; abort when over max_bytes."""
    # Prefer Content-Length early reject when present and trusted enough.
    try:
        headers = getattr(resp, "headers", None)
        cl = headers.get("Content-Length") if headers is not None else None
        if cl is not None and str(cl).strip().isdigit():
            n = int(str(cl).strip())
            if n > max_bytes:
                raise FetchError(
                    "url_too_large",
                    detail=f"Content-Length {n} exceeds max {max_bytes}",
                )
    except FetchError:
        raise
    except Exception:  # noqa: BLE001 — ignore bad headers
        pass

    chunks: list[bytes] = []
    total = 0
    while True:
        try:
            chunk = resp.read(_STREAM_CHUNK)
        except TimeoutError as exc:
            raise FetchError("url_timeout", detail="read timeout") from exc
        except Exception as exc:  # noqa: BLE001
            raise FetchError(
                "url_fetch_failed",
                detail=f"read_error:{type(exc).__name__}",
            ) from exc
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise FetchError(
                "url_too_large",
                detail=f"download exceeded max {max_bytes} bytes",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _is_redirect_status(code: int) -> bool:
    return code in (301, 302, 303, 307, 308)


def fetch_url_bytes(
    url: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_redirects: int = MAX_REDIRECTS,
    max_bytes: int | None = None,
    urlopen: UrlOpenFn | None = None,
    getaddrinfo: GetAddrInfoFn | None = None,
) -> FetchedBytes:
    """SSRF-aware HTTPS GET → bytes (no MediaStore write).

    Default path **pins** TCP to the allowlisted resolve IP and sets Host + TLS
    SNI to the original hostname (no second DNS at connect). Injectable
    ``urlopen`` is for hermetic tests and still runs resolve/IP checks first.

    Raises:
        FetchError: stable ``reason`` codes for tools
            (url_invalid, url_ssrf_blocked, url_redirect_blocked,
             url_timeout, url_too_large, url_fetch_failed).
    """
    budget = URL_MAX_BYTES if max_bytes is None else int(max_bytes)
    if budget < 1:
        raise FetchError("url_invalid", detail="max_bytes must be >= 1")
    timeout = float(timeout_s)
    use_inject = urlopen is not None
    open_fn = urlopen if use_inject else None

    current = url.strip()
    hops = 0
    seen: set[str] = set()

    while True:
        if current in seen:
            safe = redacted_url_for_log(current)
            _LOG.info("media fetch url_redirect_blocked url=%s detail=loop", safe)
            raise FetchError("url_redirect_blocked", detail="redirect loop")
        seen.add(current)
        try:
            _parsed, allow_ips = _validate_url_target(
                current, getaddrinfo=getaddrinfo
            )
        except FetchError as exc:
            # SSRF / scheme / DNS — log redacted URL only (no query secrets).
            safe = redacted_url_for_log(current)
            _LOG.info(
                "media fetch %s url=%s detail=%s",
                exc.reason,
                safe,
                (exc.detail or "-")[:120],
            )
            raise
        safe = redacted_url_for_log(current)
        connect_ip = allow_ips[0]

        req_headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "image/*,audio/*,video/*,application/pdf,*/*;q=0.8",
            # Host is also set by the connection for pin path; explicit for inject.
            "Host": _parsed.hostname or "",
        }
        # Prefer original host:port when non-default port for Host header.
        if _parsed.hostname and _parsed.port and _parsed.port != 443:
            req_headers["Host"] = f"{_parsed.hostname}:{_parsed.port}"

        try:
            if use_inject:
                assert open_fn is not None
                req = urllib.request.Request(
                    current,
                    data=None,
                    headers=req_headers,
                    method="GET",
                )
                resp = open_fn(req, timeout=timeout)
            else:
                resp = _pinned_https_open(
                    current,
                    connect_ip=connect_ip,
                    timeout=timeout,
                    headers=req_headers,
                )
        except FetchError:
            raise
        except TimeoutError as exc:
            _LOG.info("media fetch timeout url=%s", safe)
            raise FetchError("url_timeout", detail="connect/read timeout") from exc
        except urllib.error.HTTPError as exc:
            code = int(exc.code)
            try:
                if _is_redirect_status(code):
                    loc = None
                    try:
                        loc = exc.headers.get("Location") if exc.headers else None
                    finally:
                        _close_http_error(exc)
                    hops += 1
                    if hops > max_redirects:
                        raise FetchError(
                            "url_redirect_blocked",
                            detail=f"exceeded max_redirects={max_redirects}",
                        )
                    current = _join_redirect(current, loc or "")
                    # Re-loop with revalidation of next hop.
                    continue
                _LOG.info("media fetch http_error code=%s url=%s", code, safe)
                raise FetchError(
                    "url_fetch_failed",
                    detail=f"http_{code}",
                ) from exc
            finally:
                # Non-redirect path: always close body (redirect already closed).
                if not _is_redirect_status(code):
                    _close_http_error(exc)
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            # socket.timeout often wrapped
            if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
                _LOG.info("media fetch timeout url=%s", safe)
                raise FetchError("url_timeout", detail="connect/read timeout") from exc
            _LOG.info(
                "media fetch network_error url=%s err=%s",
                safe,
                type(reason).__name__ if reason is not None else type(exc).__name__,
            )
            raise FetchError(
                "url_fetch_failed",
                detail=f"network:{type(reason).__name__ if reason is not None else type(exc).__name__}",
            ) from exc
        except TimeoutError as exc:
            _LOG.info("media fetch timeout url=%s", safe)
            raise FetchError("url_timeout", detail="connect/read timeout") from exc
        except Exception as exc:  # noqa: BLE001
            _LOG.info(
                "media fetch failed url=%s err=%s",
                safe,
                type(exc).__name__,
            )
            raise FetchError(
                "url_fetch_failed",
                detail=f"request_failed:{type(exc).__name__}",
            ) from exc

        try:
            code = int(getattr(resp, "status", None) or resp.getcode() or 0)
            if _is_redirect_status(code):
                headers = getattr(resp, "headers", None)
                loc = headers.get("Location") if headers is not None else None
                hops += 1
                if hops > max_redirects:
                    raise FetchError(
                        "url_redirect_blocked",
                        detail=f"exceeded max_redirects={max_redirects}",
                    )
                current = _join_redirect(current, loc or "")
                continue
            if code != 200:
                raise FetchError("url_fetch_failed", detail=f"http_{code}")

            headers = getattr(resp, "headers", None)
            cd = headers.get("Content-Disposition") if headers is not None else None
            ct = headers.get("Content-Type") if headers is not None else None
            fname = _filename_from_content_disposition(cd) or _filename_from_url(current)
            claimed = _content_type_mime(ct)
            data = _read_body_limited(resp, budget)
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass

        if not data:
            _LOG.info("media fetch url_fetch_failed url=%s detail=empty_body", safe)
            raise FetchError("url_fetch_failed", detail="empty_body")

        _LOG.info(
            "media fetch ok url=%s bytes=%d",
            redacted_url_for_log(current),
            len(data),
        )
        return FetchedBytes(
            data=data,
            filename=fname,
            claimed_mime=claimed,
            final_url=current,
        )


def reject_non_media_payload(data: bytes, mime: str, kind: str) -> None:
    """Raise url_content_type_rejected for clearly non-media payloads."""
    if kind in ("image", "audio", "video"):
        return
    if kind == "file" and mime in (
        "application/pdf",
        "text/plain",
        "text/markdown",
        "application/json",
        "text/csv",
    ):
        # Allow common document binaries into inventory (view_media supports file).
        return
    head = data.lstrip()[:64].lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
        raise FetchError(
            "url_content_type_rejected",
            detail="response looks like HTML, not media",
        )
    if mime.startswith("text/html") or mime in (
        "application/xhtml+xml",
        "text/javascript",
        "application/javascript",
    ):
        raise FetchError(
            "url_content_type_rejected",
            detail=f"content type not media-like: {mime}",
        )
    # Unknown octet-stream file — allow (inventory); caller may still view.
    if kind == "file":
        return
    raise FetchError(
        "url_content_type_rejected",
        detail=f"unsupported kind after sniff: {kind}",
    )


def fetch_url_to_media(
    url: str,
    *,
    paths: ElyraPaths | None = None,
    origin: str = "view",
    uploader_user_id: str | None = "operator",
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_redirects: int = MAX_REDIRECTS,
    max_bytes: int | None = None,
    urlopen: UrlOpenFn | None = None,
    getaddrinfo: GetAddrInfoFn | None = None,
) -> Attachment:
    """SSRF-aware HTTPS fetch → MediaStore.put_bytes (origin default ``view``).

    Reuses existing meta by sha when present (content-idempotent). Raises
    ``FetchError`` with stable reasons for the tool layer.
    """
    layout = paths or resolve_paths()
    fetched = fetch_url_bytes(
        url,
        timeout_s=timeout_s,
        max_redirects=max_redirects,
        max_bytes=max_bytes,
        urlopen=urlopen,
        getaddrinfo=getaddrinfo,
    )
    mime, kind = sniff_mime_and_kind(
        fetched.data,
        filename=fetched.filename,
        claimed_mime=fetched.claimed_mime,
    )
    reject_non_media_payload(fetched.data, mime, kind)
    if kind == "tts_cache":
        raise FetchError(
            "url_content_type_rejected",
            detail="tts_cache cannot be fetched for view",
        )

    kind_limit = max_bytes_for_kind(kind)
    budget = URL_MAX_BYTES if max_bytes is None else int(max_bytes)
    limit = min(kind_limit, budget)
    if len(fetched.data) > limit:
        raise FetchError(
            "url_too_large",
            detail=f"{len(fetched.data)} bytes exceeds {kind} max {limit}",
        )

    store = MediaStore(layout)
    sha = hashlib.sha256(fetched.data).hexdigest()
    existing = store.find_first_by_sha256(sha)
    if existing is not None and existing.kind != "tts_cache":
        return existing

    try:
        att = store.put_bytes(
            fetched.data,
            filename=fetched.filename,
            mime=mime,
            kind=kind,
            origin=origin,
            uploader_user_id=uploader_user_id,
        )
    except ValueError as exc:
        raise FetchError("url_fetch_failed", detail=str(exc)) from exc
    except OSError as exc:
        raise FetchError(
            "url_fetch_failed",
            detail=f"os_error:{type(exc).__name__}",
        ) from exc
    _LOG.info(
        "media fetch stored att_id=%s kind=%s bytes=%d url=%s",
        att.id,
        att.kind,
        int(att.byte_size or 0),
        redacted_url_for_log(url),
    )
    return att


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "MAX_REDIRECTS",
    "URL_MAX_BYTES",
    "FetchError",
    "FetchedBytes",
    "fetch_url_bytes",
    "fetch_url_to_media",
    "is_blocked_ip",
    "redacted_source_url",
    "redacted_url_for_log",
    "reject_non_media_payload",
]
