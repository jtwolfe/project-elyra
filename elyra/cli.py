"""Elyra operator CLI — start + headless xAI OAuth auth.

Config merge (start): defaults < elyra.toml < data/runtime/provider.json < explicit CLI.
Hermetic UI path is ``--stub-llm`` only. ``provider=local`` fails closed
(no local inference process this pass).

Auth commands are **paths-only** (no supervisor / no half-init ProviderRuntime):
``elyra auth login`` uses device-code + ``persist_oauth_login``; logout deletes
the oauth bundle; status prints non-secret public meta. Live chat rebind after
login against a running instance requires restart or Glass login.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from elyra.config import resolve_paths
from elyra.llm.auth import VALID_SOURCES
from elyra.llm.oauth_store import (
    delete_oauth_bundle,
    persist_oauth_login,
    public_meta,
)
from elyra.llm.provider_prefs import load_provider_prefs
from elyra.llm.xai_oauth import (
    DEFAULT_HTTP_TIMEOUT_S,
    DETAIL_NETWORK,
    DETAIL_OAUTH_DEVICE_EXPIRED,
    DETAIL_OAUTH_REFRESH_FAILED,
    UrlOpenFn,
    bundle_from_token_success,
    next_poll_interval,
    poll_device_token,
    request_device_code,
)
from elyra.runtime.config import load_merged_settings, runtime_config_from_settings
from elyra.runtime.provider_runtime import (
    credential_detail_message,
    format_usage_posture,
)
from elyra.runtime.supervisor import ElyraSupervisor

# Restart / Glass note for operators after paths-only login (live rebind).
_LIVE_REBIND_NOTE = (
    "Note: tokens are on disk only. Cold `elyra start` picks them up. "
    "If an instance is already running against this data_dir, restart it "
    "or use Glass “Log in with xAI” for live rebind."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="elyra", description="Elyra operator CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser(
        "start",
        help="Start API, Web UI, presence worker (xAI Grok by default)",
    )
    start.add_argument(
        "--provider",
        choices=("xai", "local"),
        default=None,
        help="Override provider (default: settings / xai)",
    )
    start.add_argument(
        "--model",
        default=None,
        help="Override wire model id (e.g. grok-4.5)",
    )
    start.add_argument(
        "--credential-source",
        choices=tuple(sorted(VALID_SOURCES)),
        default=None,
        help="Override active credential source",
    )
    start.add_argument(
        "--no-usage-meter",
        action="store_true",
        help="Disable hierarchical usage meter (debug)",
    )
    start.add_argument(
        "--stub-llm",
        action="store_true",
        help="Use StubChatClient (only hermetic UI path; never remote calls)",
    )
    start.add_argument("--api-host", default="127.0.0.1")
    start.add_argument("--api-port", type=int, default=8787)

    auth = sub.add_parser(
        "auth",
        help="xAI OAuth login / logout / status (paths-only; no supervisor)",
    )
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)

    login = auth_sub.add_parser(
        "login",
        help=(
            "Headless xAI device-code login (persist_oauth_login only; "
            "no live ProviderRuntime rebind)"
        ),
        description=(
            "Start xAI OIDC device-code login on stdout, poll until consent "
            "completes, then write tokens via persist_oauth_login (disk + "
            "optional credential_source=xai_oauth). Does not construct a "
            "ProviderRuntime. "
            + _LIVE_REBIND_NOTE
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    login.add_argument(
        "--no-activate",
        action="store_true",
        help=(
            "Write tokens only; do not set credential_source=xai_oauth "
            "(default: activate / switch source)"
        ),
    )
    login.add_argument(
        "--timeout-s",
        type=float,
        default=None,
        metavar="N",
        help=(
            "Max seconds to wait for browser consent "
            "(default: server device expires_in)"
        ),
    )

    auth_sub.add_parser(
        "logout",
        help="Delete Elyra xAI OAuth token bundle (xai_oauth.json)",
        description=(
            "Remove data/secrets/xai_oauth.json (+ tmp/lock). Paths-only — "
            "does not rebuild a running chat stack. Restart or Glass logout "
            "if an instance is already running."
        ),
    )

    auth_sub.add_parser(
        "status",
        help="Show non-secret xAI OAuth status for this home",
        description=(
            "Print public OAuth meta (configured, email, expires_at, "
            "reauth_required, auth_method) and active credential_source from "
            "prefs. Never prints tokens or device_code."
        ),
    )

    return parser


def _print_startup_posture(sup: ElyraSupervisor) -> None:
    """Print provider/usage posture after supervisor.start()."""
    paths = sup.paths
    config = sup.config
    state = sup.state
    pr = sup.provider_runtime

    print(f"Elyra home:  {paths.home}")
    print(f"Web UI:      http://{config.api_host}:{config.api_port}/")
    print(
        f"Provider:    {state.provider_name}  "
        f"(model={state.model} · source={state.credential_source} · "
        f"credential_ok={str(state.credential_ok).lower()})"
    )
    if not state.credential_ok and state.provider_name == "xai":
        msg = credential_detail_message(state.credential_detail)
        if msg:
            print(f"Credential:  {msg}")
    continuous = "on" if config.continuous_enabled else "off"
    print(f"Continuous:  {continuous}")
    meter = pr.meter if pr is not None else None
    usage_line = format_usage_posture(meter, enabled=config.usage.enabled)
    print(f"Usage:       {usage_line}")
    # Chat posture (provider-neutral chat_* fields — KD14).
    if state.chat_error == "stub_llm":
        chat = "stub"
    elif state.chat_error == "local_not_implemented":
        chat = "local_not_implemented"
    elif state.chat_ready:
        chat = "ready"
    else:
        chat = "off"
    print(f"chat:        {chat}")


def _cmd_start(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    paths = resolve_paths()
    # Ensure data/runtime exists so provider.json load is well-defined.
    paths.ensure_data_dirs()

    settings = load_merged_settings(
        paths.home,
        paths.data_dir,
        provider=args.provider,
        model=args.model,
        credential_source=args.credential_source,
        no_usage_meter=bool(args.no_usage_meter),
        # api_host/port keep argparse defaults (explicit CLI surface).
        api_host=args.api_host,
        api_port=args.api_port,
    )

    use_stub = bool(args.stub_llm)
    config = runtime_config_from_settings(
        settings,
        stub_llm=use_stub,
        data_dir=paths.data_dir,
    )

    sup = ElyraSupervisor(
        paths=paths,
        config=config,
        use_stub_llm=use_stub,
    )
    sup.start()
    _print_startup_posture(sup)
    sup.serve_until_stopped()
    return 0


def run_auth_login(
    data_dir: Path,
    *,
    activate: bool = True,
    timeout_s: float | None = None,
    urlopen: UrlOpenFn | None = None,
    sleep: Callable[[float], None] = time.sleep,
    out: TextIO | None = None,
    err: TextIO | None = None,
    http_timeout_s: float = DEFAULT_HTTP_TIMEOUT_S,
) -> int:
    """Headless device-code login → ``persist_oauth_login`` only.

    Returns process exit code (0 success, non-zero failure). Never prints
    ``device_code`` / tokens. Does not construct ProviderRuntime.
    """
    stdout = out if out is not None else sys.stdout
    stderr = err if err is not None else sys.stderr

    print("xAI OAuth device login (Elyra)", file=stdout)
    print(f"data_dir: {data_dir}", file=stdout)
    print(
        "Consent screen may say “Grok Build” (shared public OAuth client).",
        file=stdout,
    )
    print(file=stdout)

    try:
        device = request_device_code(timeout=http_timeout_s, urlopen=urlopen)
    except (OSError, ValueError) as exc:
        print(
            f"FAIL device start: {type(exc).__name__}: {exc}",
            file=stderr,
        )
        return 2

    print(f"user_code:                 {device.user_code}", file=stdout)
    print(f"verification_uri:          {device.verification_uri}", file=stdout)
    if device.verification_uri_complete:
        print(
            f"verification_uri_complete: {device.verification_uri_complete}",
            file=stdout,
        )
    print(f"expires_in:                {device.expires_in}s", file=stdout)
    print(f"interval:                  {device.interval}s", file=stdout)
    print(file=stdout)
    print(
        "Open the verification URL in a browser and enter the user code.",
        file=stdout,
    )
    print("Polling for consent… (Ctrl-C to abort)", file=stdout)

    wall = float(device.expires_in)
    if timeout_s is not None and timeout_s > 0:
        wall = min(wall, float(timeout_s))
    deadline = time.monotonic() + max(1.0, wall)
    interval = max(1, int(device.interval))

    try:
        while time.monotonic() < deadline:
            try:
                result = poll_device_token(
                    device.device_code,
                    timeout=http_timeout_s,
                    urlopen=urlopen,
                )
            except OSError as exc:
                print(
                    f"  … network error ({type(exc).__name__}); retry",
                    file=stdout,
                )
                interval = next_poll_interval(interval, slow_down=False)
                sleep(float(interval))
                continue

            if result.ok and result.access_token:
                try:
                    bundle = bundle_from_token_success(result, auth_method="device_code")
                    meta = persist_oauth_login(
                        data_dir, bundle, activate=activate
                    )
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"FAIL persist: {type(exc).__name__}: {exc}",
                        file=stderr,
                    )
                    return 4

                print(file=stdout)
                print("Login OK (tokens written; not printed)", file=stdout)
                print(f"  configured:       {str(meta.configured).lower()}", file=stdout)
                print(f"  email:            {meta.email or '(none)'}", file=stdout)
                print(f"  expires_at:       {meta.expires_at or '(none)'}", file=stdout)
                print(f"  auth_method:      {meta.auth_method or '(none)'}", file=stdout)
                print(
                    f"  reauth_required:  {str(meta.reauth_required).lower()}",
                    file=stdout,
                )
                print(
                    f"  activate:         {str(activate).lower()} "
                    f"(credential_source={'xai_oauth' if activate else 'unchanged'})",
                    file=stdout,
                )
                print(file=stdout)
                print(_LIVE_REBIND_NOTE, file=stdout)
                return 0

            if result.pending or result.detail == DETAIL_NETWORK:
                slow = bool(result.slow_down)
                interval = next_poll_interval(interval, slow_down=slow)
                tag = "slow_down" if slow else (result.detail or "pending")
                print(f"  … {tag}; sleep {interval}s", file=stdout)
                sleep(float(interval))
                continue

            detail = result.detail or DETAIL_OAUTH_REFRESH_FAILED
            print(f"FAIL: {detail}", file=stderr)
            return 3

    except KeyboardInterrupt:
        print("\nAborted.", file=stderr)
        return 130

    print(f"FAIL: {DETAIL_OAUTH_DEVICE_EXPIRED}", file=stderr)
    return 5


def run_auth_logout(
    data_dir: Path,
    *,
    out: TextIO | None = None,
) -> int:
    """Delete oauth bundle (paths-only). Exit 0 even if already absent."""
    stdout = out if out is not None else sys.stdout
    removed = delete_oauth_bundle(data_dir)
    meta = public_meta(data_dir)
    print("xAI OAuth logout", file=stdout)
    print(f"data_dir:          {data_dir}", file=stdout)
    print(f"bundle_removed:    {str(removed).lower()}", file=stdout)
    print(f"oauth_configured:  {str(meta.configured).lower()}", file=stdout)
    print(
        "Paths-only: running instances need restart or Glass logout for live rebind.",
        file=stdout,
    )
    return 0


def run_auth_status(
    data_dir: Path,
    *,
    out: TextIO | None = None,
) -> int:
    """Print non-secret oauth status + prefs credential_source."""
    stdout = out if out is not None else sys.stdout
    meta = public_meta(data_dir)
    prefs = load_provider_prefs(data_dir)
    cs = prefs.credential_source or "(unset)"

    print("xAI OAuth status", file=stdout)
    print(f"data_dir:           {data_dir}", file=stdout)
    print(f"oauth_configured:   {str(meta.configured).lower()}", file=stdout)
    print(f"email:              {meta.email or '(none)'}", file=stdout)
    print(f"expires_at:         {meta.expires_at or '(none)'}", file=stdout)
    print(f"updated_at:         {meta.updated_at or '(none)'}", file=stdout)
    print(f"auth_method:        {meta.auth_method or '(none)'}", file=stdout)
    print(f"reauth_required:    {str(meta.reauth_required).lower()}", file=stdout)
    print(f"credential_source:  {cs}", file=stdout)
    return 0


def _cmd_auth(args: argparse.Namespace) -> int:
    paths = resolve_paths()
    paths.ensure_data_dirs()
    data_dir = paths.data_dir

    if args.auth_command == "login":
        return run_auth_login(
            data_dir,
            activate=not bool(args.no_activate),
            timeout_s=args.timeout_s,
        )
    if args.auth_command == "logout":
        return run_auth_logout(data_dir)
    if args.auth_command == "status":
        return run_auth_status(data_dir)
    return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "start":
        return _cmd_start(args)
    if args.command == "auth":
        return _cmd_auth(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
