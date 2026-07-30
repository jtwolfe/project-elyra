#!/usr/bin/env python3
"""Operator smoke: xAI OIDC discovery + device authorization start.

Not part of the Elyra runtime and not CI-blocking. Proves the OpenClaw-compatible
public client_id + discovery endpoints accept a device-code start.

Usage:
  python3 scripts/prototype_xai_oauth_device_smoke.py
  python3 scripts/prototype_xai_oauth_device_smoke.py --full-login   # poll until tokens
  python3 scripts/prototype_xai_oauth_device_smoke.py --no-device    # discovery only

Never prints access_token, refresh_token, or device_code.
Prints user_code + verification URIs for the operator.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Allow running from a checkout without install.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from elyra.llm.xai_oauth import (  # noqa: E402
    XAI_OAUTH_CLIENT_ID,
    XAI_OAUTH_SCOPE,
    XAI_OIDC_DISCOVERY,
    XAI_OIDC_ISSUER,
    clear_discovery_cache,
    fetch_discovery,
    next_poll_interval,
    poll_device_token,
    request_device_code,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-device",
        action="store_true",
        help="Only GET discovery; do not start device authorization",
    )
    parser.add_argument(
        "--full-login",
        action="store_true",
        help="After device start, poll token endpoint until success/error (operator completes browser)",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=30.0,
        help="HTTP timeout seconds (default 30)",
    )
    args = parser.parse_args(argv)

    print("=== xAI OIDC device smoke (Elyra PR1) ===")
    print(f"issuer:     {XAI_OIDC_ISSUER}")
    print(f"discovery:  {XAI_OIDC_DISCOVERY}")
    print(f"client_id:  {XAI_OAUTH_CLIENT_ID}")
    print(f"scope:      {XAI_OAUTH_SCOPE}")
    print()

    clear_discovery_cache()
    try:
        doc = fetch_discovery(timeout=args.timeout_s, use_cache=False)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL discovery: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print("discovery OK")
    print(f"  issuer:                         {doc.issuer}")
    print(f"  device_authorization_endpoint:  {doc.device_authorization_endpoint}")
    print(f"  token_endpoint:                 {doc.token_endpoint}")
    if not doc.device_authorization_endpoint or not doc.token_endpoint:
        print("FAIL: missing device or token endpoint", file=sys.stderr)
        return 2
    if "device" not in doc.device_authorization_endpoint and not doc.raw:
        print("NOTE: using compiled-in fallbacks (discovery body empty)")

    # Pin observed fields for operators / fixture comments after first success.
    if doc.raw:
        keys = sorted(str(k) for k in doc.raw.keys())
        print(f"  discovery keys ({len(keys)}): {', '.join(keys[:12])}{'…' if len(keys) > 12 else ''}")

    if args.no_device:
        print("OK (discovery only)")
        return 0

    print()
    try:
        device = request_device_code(
            timeout=args.timeout_s,
            discovery=doc,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL device start: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    print("device authorization OK (device_code held in memory, not printed)")
    print(f"  user_code:                   {device.user_code}")
    print(f"  verification_uri:            {device.verification_uri}")
    if device.verification_uri_complete:
        print(f"  verification_uri_complete:   {device.verification_uri_complete}")
    print(f"  expires_in:                  {device.expires_in}s")
    print(f"  interval:                    {device.interval}s")
    print()
    print("Open the verification URL in a browser and enter the user code.")
    print("Consent screen may say “Grok Build” (shared public OAuth client).")

    if not args.full_login:
        print("OK (discovery + device start). Re-run with --full-login to poll tokens.")
        return 0

    print()
    print("Polling token endpoint… (Ctrl-C to abort)")
    interval = device.interval
    deadline = time.monotonic() + float(device.expires_in)
    while time.monotonic() < deadline:
        try:
            result = poll_device_token(
                device.device_code,
                timeout=args.timeout_s,
                discovery=doc,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL poll: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 4

        if result.ok:
            email, _sub = (None, None)
            if result.id_token:
                from elyra.llm.xai_oauth import email_and_subject_from_id_token

                email, _sub = email_and_subject_from_id_token(result.id_token)
            print("token grant OK")
            print(f"  access_token length:  {len(result.access_token or '')}")
            print(f"  refresh_token length: {len(result.refresh_token or '')}")
            print(f"  expires_in:           {result.expires_in}")
            print(f"  email (id_token):     {email or '(none)'}")
            print("OK (full login). Tokens not printed; not written to disk by this smoke.")
            return 0

        if result.pending:
            interval = next_poll_interval(interval, slow_down=result.slow_down)
            tag = "slow_down" if result.slow_down else "authorization_pending"
            print(f"  … {tag}; sleep {interval}s")
            time.sleep(interval)
            continue

        print(
            f"FAIL terminal: detail={result.detail} error={result.error}",
            file=sys.stderr,
        )
        return 5

    print("FAIL: device flow expired before completion", file=sys.stderr)
    return 6


if __name__ == "__main__":
    raise SystemExit(main())
