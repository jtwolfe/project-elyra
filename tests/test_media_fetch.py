"""Hermetic tests for SSRF-safe media URL fetch (KD-V18 / PR5).

Injectable urlopen + getaddrinfo — no real network. Covers private IP blocks,
success path, size cap, timeout, https-only, redirect revalidation.
"""

from __future__ import annotations

import io
import ipaddress
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.media.fetch import (
    URL_MAX_BYTES,
    FetchError,
    FetchedBytes,
    fetch_url_bytes,
    fetch_url_to_media,
    is_blocked_ip,
    redacted_source_url,
    redacted_url_for_log,
)
from elyra.media.store import MediaStore

FIXTURE_PNG = Path(__file__).parent / "fixtures" / "media" / "1x1.png"


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


class _FakeHeaders(dict):
    def get_content_charset(self, failobj=None):  # noqa: ANN001
        return failobj


class _FakeResp:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._buf = io.BytesIO(body)
        self.status = status
        self.headers = _FakeHeaders(headers or {})

    def getcode(self) -> int:
        return self.status

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def close(self) -> None:
        self._buf.close()


def _public_getaddrinfo(host: str, port: int, *args: Any, **kwargs: Any):
    """Resolve hermetic hostnames to a public test IP (no real DNS)."""
    # Always 8.8.8.8-like public for any non-literal host in tests.
    return [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            0,
            "",
            ("93.184.216.34", port if isinstance(port, int) else 443),
        )
    ]


def _png_urlopen_factory(
    body: bytes | None = None,
    *,
    content_type: str = "image/png",
    status: int = 200,
    extra_headers: dict[str, str] | None = None,
):
    data = body if body is not None else FIXTURE_PNG.read_bytes()
    headers = {"Content-Type": content_type, "Content-Length": str(len(data))}
    if extra_headers:
        headers.update(extra_headers)

    def urlopen(req: urllib.request.Request, timeout: float = 20.0) -> _FakeResp:
        return _FakeResp(data, status=status, headers=headers)

    return urlopen


# ---------------------------------------------------------------------------
# IP blocklist unit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip_str",
    [
        "127.0.0.1",
        "10.0.0.5",
        "10.255.255.255",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "169.254.0.1",
        # CGNAT / shared space 100.64/10 (incl. Alibaba metadata 100.100.100.200)
        "100.64.0.1",
        "100.100.100.200",
        "100.127.255.255",
        "::1",
        "fc00::1",
        "fe80::1",
        "0.0.0.0",
    ],
)
def test_is_blocked_ip(ip_str: str) -> None:
    assert is_blocked_ip(ipaddress.ip_address(ip_str)) is True


@pytest.mark.parametrize("ip_str", ["93.184.216.34", "1.1.1.1", "8.8.8.8", "100.128.0.1"])
def test_public_ip_not_blocked(ip_str: str) -> None:
    assert is_blocked_ip(ipaddress.ip_address(ip_str)) is False


def test_ipv4_mapped_loopback_blocked() -> None:
    assert is_blocked_ip(ipaddress.ip_address("::ffff:127.0.0.1")) is True


def test_ipv4_mapped_cgnat_blocked() -> None:
    assert is_blocked_ip(ipaddress.ip_address("::ffff:100.64.0.1")) is True
    assert is_blocked_ip(ipaddress.ip_address("::ffff:100.100.100.200")) is True


# ---------------------------------------------------------------------------
# Scheme / literal SSRF
# ---------------------------------------------------------------------------


def test_http_rejected() -> None:
    with pytest.raises(FetchError) as ei:
        fetch_url_bytes("http://example.com/a.png", getaddrinfo=_public_getaddrinfo)
    assert ei.value.reason == "url_invalid"


def test_file_scheme_rejected() -> None:
    with pytest.raises(FetchError) as ei:
        fetch_url_bytes("file:///etc/passwd")
    assert ei.value.reason == "url_invalid"


def test_loopback_literal_blocked() -> None:
    with pytest.raises(FetchError) as ei:
        fetch_url_bytes("https://127.0.0.1/secret")
    assert ei.value.reason == "url_ssrf_blocked"


def test_10_x_literal_blocked() -> None:
    with pytest.raises(FetchError) as ei:
        fetch_url_bytes("https://10.1.2.3/x.png")
    assert ei.value.reason == "url_ssrf_blocked"


def test_metadata_ip_literal_blocked() -> None:
    with pytest.raises(FetchError) as ei:
        fetch_url_bytes("https://169.254.169.254/latest/meta-data/")
    assert ei.value.reason == "url_ssrf_blocked"


def test_cgnat_literal_blocked() -> None:
    with pytest.raises(FetchError) as ei:
        fetch_url_bytes("https://100.64.0.1/x.png")
    assert ei.value.reason == "url_ssrf_blocked"


def test_alibaba_metadata_literal_blocked() -> None:
    with pytest.raises(FetchError) as ei:
        fetch_url_bytes("https://100.100.100.200/latest/meta-data/")
    assert ei.value.reason == "url_ssrf_blocked"


def test_hostname_resolving_to_private_blocked() -> None:
    def gai(host: str, port: int, *a: Any, **k: Any):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                0,
                "",
                ("10.0.0.9", 443),
            )
        ]

    with pytest.raises(FetchError) as ei:
        fetch_url_bytes(
            "https://evil.internal/a.png",
            getaddrinfo=gai,
            urlopen=_png_urlopen_factory(),  # must not be called
        )
    assert ei.value.reason == "url_ssrf_blocked"


def test_hostname_resolving_to_metadata_blocked() -> None:
    def gai(host: str, port: int, *a: Any, **k: Any):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                0,
                "",
                ("169.254.169.254", 443),
            )
        ]

    with pytest.raises(FetchError) as ei:
        fetch_url_bytes("https://metadata.google/x", getaddrinfo=gai)
    assert ei.value.reason == "url_ssrf_blocked"


def test_credentials_in_url_rejected() -> None:
    with pytest.raises(FetchError) as ei:
        fetch_url_bytes(
            "https://user:pass@example.com/a.png",
            getaddrinfo=_public_getaddrinfo,
        )
    assert ei.value.reason == "url_invalid"


# ---------------------------------------------------------------------------
# Success / size / timeout
# ---------------------------------------------------------------------------


def test_fetch_bytes_success() -> None:
    data = FIXTURE_PNG.read_bytes()
    result = fetch_url_bytes(
        "https://example.com/cat.png",
        urlopen=_png_urlopen_factory(data),
        getaddrinfo=_public_getaddrinfo,
    )
    assert isinstance(result, FetchedBytes)
    assert result.data == data
    assert result.filename == "cat.png"
    assert result.claimed_mime == "image/png"


def test_fetch_url_to_media_success(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    data = FIXTURE_PNG.read_bytes()
    att = fetch_url_to_media(
        "https://example.com/cat.png",
        paths=paths,
        urlopen=_png_urlopen_factory(data),
        getaddrinfo=_public_getaddrinfo,
    )
    assert att.kind == "image"
    assert att.origin == "view"
    assert att.sha256
    store = MediaStore(paths)
    assert store.get(att.id) is not None
    # Idempotent re-fetch same bytes → same meta id.
    att2 = fetch_url_to_media(
        "https://example.com/cat.png",
        paths=paths,
        urlopen=_png_urlopen_factory(data),
        getaddrinfo=_public_getaddrinfo,
    )
    assert att2.id == att.id


def test_size_cap_content_length() -> None:
    def urlopen(req: urllib.request.Request, timeout: float = 20.0) -> _FakeResp:
        return _FakeResp(
            b"x" * 100,
            headers={"Content-Type": "image/png", "Content-Length": "999999999"},
        )

    with pytest.raises(FetchError) as ei:
        fetch_url_bytes(
            "https://example.com/huge.png",
            urlopen=urlopen,
            getaddrinfo=_public_getaddrinfo,
            max_bytes=1024,
        )
    assert ei.value.reason == "url_too_large"


def test_size_cap_streamed_body() -> None:
    big = b"\x89PNG\r\n\x1a\n" + b"Z" * 5000

    def urlopen(req: urllib.request.Request, timeout: float = 20.0) -> _FakeResp:
        # No Content-Length — stream until over cap.
        return _FakeResp(big, headers={"Content-Type": "image/png"})

    with pytest.raises(FetchError) as ei:
        fetch_url_bytes(
            "https://example.com/stream.png",
            urlopen=urlopen,
            getaddrinfo=_public_getaddrinfo,
            max_bytes=1024,
        )
    assert ei.value.reason == "url_too_large"


def test_timeout_raises_url_timeout() -> None:
    def urlopen(req: urllib.request.Request, timeout: float = 20.0) -> _FakeResp:
        raise TimeoutError("simulated")

    with pytest.raises(FetchError) as ei:
        fetch_url_bytes(
            "https://example.com/slow.png",
            urlopen=urlopen,
            getaddrinfo=_public_getaddrinfo,
        )
    assert ei.value.reason == "url_timeout"


def test_urlerror_timeout_maps_to_url_timeout() -> None:
    def urlopen(req: urllib.request.Request, timeout: float = 20.0) -> _FakeResp:
        raise urllib.error.URLError(TimeoutError("x"))

    with pytest.raises(FetchError) as ei:
        fetch_url_bytes(
            "https://example.com/slow2.png",
            urlopen=urlopen,
            getaddrinfo=_public_getaddrinfo,
        )
    assert ei.value.reason == "url_timeout"


def test_http_error_maps_to_fetch_failed() -> None:
    def urlopen(req: urllib.request.Request, timeout: float = 20.0) -> _FakeResp:
        raise urllib.error.HTTPError(
            "https://example.com/x",
            404,
            "Not Found",
            hdrs=_FakeHeaders(),  # type: ignore[arg-type]
            fp=io.BytesIO(b""),
        )

    with pytest.raises(FetchError) as ei:
        fetch_url_bytes(
            "https://example.com/missing.png",
            urlopen=urlopen,
            getaddrinfo=_public_getaddrinfo,
        )
    assert ei.value.reason == "url_fetch_failed"
    assert "404" in (ei.value.detail or "")


# ---------------------------------------------------------------------------
# Redirect revalidation
# ---------------------------------------------------------------------------


def test_redirect_to_private_ip_blocked() -> None:
    calls: list[str] = []

    def urlopen(req: urllib.request.Request, timeout: float = 20.0) -> _FakeResp:
        calls.append(req.full_url)
        if "example.com" in req.full_url:
            raise urllib.error.HTTPError(
                req.full_url,
                302,
                "Found",
                hdrs=_FakeHeaders({"Location": "https://127.0.0.1/secret"}),  # type: ignore[arg-type]
                fp=io.BytesIO(b""),
            )
        return _FakeResp(FIXTURE_PNG.read_bytes())

    with pytest.raises(FetchError) as ei:
        fetch_url_bytes(
            "https://example.com/start.png",
            urlopen=urlopen,
            getaddrinfo=_public_getaddrinfo,
        )
    assert ei.value.reason == "url_ssrf_blocked"
    assert len(calls) == 1  # never opened loopback


def test_redirect_to_http_rejected() -> None:
    def urlopen(req: urllib.request.Request, timeout: float = 20.0) -> _FakeResp:
        raise urllib.error.HTTPError(
            req.full_url,
            302,
            "Found",
            hdrs=_FakeHeaders({"Location": "http://example.com/insecure.png"}),  # type: ignore[arg-type]
            fp=io.BytesIO(b""),
        )

    with pytest.raises(FetchError) as ei:
        fetch_url_bytes(
            "https://example.com/start",
            urlopen=urlopen,
            getaddrinfo=_public_getaddrinfo,
        )
    assert ei.value.reason == "url_invalid"


def test_redirect_to_file_rejected() -> None:
    def urlopen(req: urllib.request.Request, timeout: float = 20.0) -> _FakeResp:
        raise urllib.error.HTTPError(
            req.full_url,
            302,
            "Found",
            hdrs=_FakeHeaders({"Location": "file:///etc/passwd"}),  # type: ignore[arg-type]
            fp=io.BytesIO(b""),
        )

    with pytest.raises(FetchError) as ei:
        fetch_url_bytes(
            "https://example.com/start",
            urlopen=urlopen,
            getaddrinfo=_public_getaddrinfo,
        )
    assert ei.value.reason == "url_invalid"


def test_redirect_hostname_resolving_private_blocked() -> None:
    """Redirect Location hostname that DNS-resolves private is blocked."""
    calls: list[str] = []

    def gai(host: str, port: int, *a: Any, **k: Any):
        if host == "evil.internal":
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    0,
                    "",
                    ("10.0.0.55", 443),
                )
            ]
        return _public_getaddrinfo(host, port)

    def urlopen(req: urllib.request.Request, timeout: float = 20.0) -> _FakeResp:
        calls.append(req.full_url)
        if req.full_url.endswith("/start"):
            raise urllib.error.HTTPError(
                req.full_url,
                302,
                "Found",
                hdrs=_FakeHeaders({"Location": "https://evil.internal/x.png"}),  # type: ignore[arg-type]
                fp=io.BytesIO(b""),
            )
        return _FakeResp(FIXTURE_PNG.read_bytes())

    with pytest.raises(FetchError) as ei:
        fetch_url_bytes(
            "https://example.com/start",
            urlopen=urlopen,
            getaddrinfo=gai,
        )
    assert ei.value.reason == "url_ssrf_blocked"
    assert len(calls) == 1  # never opened evil.internal


def test_redirect_success_public() -> None:
    data = FIXTURE_PNG.read_bytes()

    def urlopen(req: urllib.request.Request, timeout: float = 20.0) -> _FakeResp:
        if req.full_url.endswith("/start"):
            raise urllib.error.HTTPError(
                req.full_url,
                302,
                "Found",
                hdrs=_FakeHeaders({"Location": "https://cdn.example.com/cat.png"}),  # type: ignore[arg-type]
                fp=io.BytesIO(b""),
            )
        return _FakeResp(
            data,
            headers={"Content-Type": "image/png", "Content-Length": str(len(data))},
        )

    result = fetch_url_bytes(
        "https://example.com/start",
        urlopen=urlopen,
        getaddrinfo=_public_getaddrinfo,
    )
    assert result.data == data
    assert result.filename == "cat.png"


def test_too_many_redirects() -> None:
    def urlopen(req: urllib.request.Request, timeout: float = 20.0) -> _FakeResp:
        raise urllib.error.HTTPError(
            req.full_url,
            302,
            "Found",
            hdrs=_FakeHeaders({"Location": req.full_url + "x"}),  # type: ignore[arg-type]
            fp=io.BytesIO(b""),
        )

    with pytest.raises(FetchError) as ei:
        fetch_url_bytes(
            "https://example.com/r",
            urlopen=urlopen,
            getaddrinfo=_public_getaddrinfo,
            max_redirects=2,
        )
    assert ei.value.reason == "url_redirect_blocked"


def test_html_content_rejected(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    html = b"<!DOCTYPE html><html><body>hi</body></html>"

    with pytest.raises(FetchError) as ei:
        fetch_url_to_media(
            "https://example.com/page",
            paths=paths,
            urlopen=_png_urlopen_factory(html, content_type="text/html"),
            getaddrinfo=_public_getaddrinfo,
        )
    assert ei.value.reason == "url_content_type_rejected"


def test_redacted_url_strips_query() -> None:
    u = "https://example.com/a.png?token=secret&x=1"
    assert "token" not in redacted_url_for_log(u)
    assert "secret" not in redacted_source_url(u)
    assert redacted_source_url(u) == "https://example.com/a.png"


def test_url_max_bytes_constant_aligned() -> None:
    assert URL_MAX_BYTES == 48 * 1024 * 1024


def test_default_path_pins_connect_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default (non-inject) path dials allowlisted IP, not a second DNS on hostname."""
    import elyra.media.fetch as fetch_mod

    data = FIXTURE_PNG.read_bytes()
    seen: dict[str, Any] = {}

    class _FakePinned:
        def __init__(
            self,
            host: str,
            port: int | None = None,
            *args: Any,
            connect_to: str | None = None,
            **kwargs: Any,
        ) -> None:
            seen["host"] = host
            seen["port"] = port
            seen["connect_to"] = connect_to
            self.host = host
            self.port = port or 443

        def request(self, method: str, path: str, headers: dict | None = None) -> None:
            seen["method"] = method
            seen["path"] = path
            seen["headers"] = dict(headers or {})

        def getresponse(self) -> _FakeResp:
            return _FakeResp(
                data,
                headers={
                    "Content-Type": "image/png",
                    "Content-Length": str(len(data)),
                },
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr(fetch_mod, "_PinnedHTTPSConnection", _FakePinned)

    result = fetch_url_bytes(
        "https://example.com/cat.png",
        getaddrinfo=_public_getaddrinfo,
        # no urlopen inject → production pin path
    )
    assert result.data == data
    assert seen["host"] == "example.com"
    assert seen["connect_to"] == "93.184.216.34"
    assert seen["path"] == "/cat.png"
    assert seen["method"] == "GET"
    # Host header is original hostname (not the dial IP).
    assert seen["headers"].get("Host") == "example.com"


def test_http_error_body_closed() -> None:
    """Non-redirect HTTPError responses are closed (Issue 3)."""
    closed: list[bool] = []

    class _CloseableFP(io.BytesIO):
        def close(self) -> None:  # noqa: A003
            closed.append(True)
            super().close()

    def urlopen(req: urllib.request.Request, timeout: float = 20.0) -> _FakeResp:
        raise urllib.error.HTTPError(
            "https://example.com/x",
            500,
            "Server Error",
            hdrs=_FakeHeaders(),  # type: ignore[arg-type]
            fp=_CloseableFP(b"err"),
        )

    with pytest.raises(FetchError) as ei:
        fetch_url_bytes(
            "https://example.com/x",
            urlopen=urlopen,
            getaddrinfo=_public_getaddrinfo,
        )
    assert ei.value.reason == "url_fetch_failed"
    assert closed == [True]
