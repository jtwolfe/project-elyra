#!/usr/bin/env python3
"""Smoke-test xAI chat completions using Grok Build's ~/.grok/auth.json session.

Not part of the Elyra runtime. Proves the SuperGrok / Grok Build OIDC access
token in auth.json can call https://api.x.ai/v1 (OpenAI-compatible chat).

Usage:
  python3 scripts/prototype_xai_grok_auth_smoke.py
  python3 scripts/prototype_xai_grok_auth_smoke.py --model grok-4.5

Never prints the raw bearer token.
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

AUTH_PATH = Path.home() / ".grok" / "auth.json"
API_BASE = "https://api.x.ai/v1"
DEFAULT_MODELS = (
    "grok-4.5",
    "grok-4.3",
    "grok-4.20-0309-non-reasoning",
    "grok-3-mini",
)


def load_session_token(path: Path = AUTH_PATH) -> tuple[str, dict]:
    if not path.is_file():
        raise SystemExit(
            f"missing {path} — run `grok login` or set up Grok Build auth first"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise SystemExit(f"unexpected auth.json root type: {type(raw).__name__}")

    # Grok Build shape: { "https://auth.x.ai::<client_id>": { key, refresh_token, ... } }
    entry_key = next(iter(raw))
    entry = raw[entry_key]
    if not isinstance(entry, dict):
        # Alternate flat shape { access_token, ... }
        entry = raw
        entry_key = "(flat)"

    token = entry.get("key") or entry.get("access_token")
    if not token or not isinstance(token, str):
        raise SystemExit(
            f"no access token field (key/access_token) in auth entry; keys={list(entry)}"
        )

    meta = {
        "auth_path": str(path),
        "entry": (entry_key[:48] + "…") if len(str(entry_key)) > 48 else entry_key,
        "auth_mode": entry.get("auth_mode"),
        "expires_at": entry.get("expires_at"),
        "email": entry.get("email"),
        "token_len": len(token),
        "token_prefix": token[:8] + "…",
    }
    exp = entry.get("expires_at")
    if isinstance(exp, str):
        try:
            exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
            meta["expires_in_s"] = int((exp_dt - datetime.now(timezone.utc)).total_seconds())
        except ValueError:
            pass
    return token, meta


def http_json(
    method: str,
    url: str,
    token: str,
    body: dict | None = None,
    *,
    timeout: float = 90.0,
) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "elyra-prototype-xai-grok-auth-smoke/0.1",
        },
    )
    try:
        with urllib.request.urlopen(
            req, timeout=timeout, context=ssl.create_default_context()
        ) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        try:
            parsed: dict = json.loads(err_body)
        except json.JSONDecodeError:
            parsed = {"raw": err_body[:800]}
        return e.code, parsed


def pick_model(listed: list[str], preferred: str | None) -> str:
    if preferred:
        return preferred
    available = set(listed)
    for cand in DEFAULT_MODELS:
        if cand in available:
            return cand
    for mid in listed:
        if not any(x in mid for x in ("image", "tts", "stt", "voice")):
            return mid
    return DEFAULT_MODELS[0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default=None, help="Model id (default: first known from /models)")
    ap.add_argument(
        "--auth",
        type=Path,
        default=AUTH_PATH,
        help=f"Path to auth.json (default: {AUTH_PATH})",
    )
    args = ap.parse_args(argv)

    token, meta = load_session_token(args.auth)
    print("=== auth (Grok Build session) ===")
    for k, v in meta.items():
        print(f"  {k}: {v}")

    print("\n=== GET /v1/models ===")
    status, models_body = http_json("GET", f"{API_BASE}/models", token)
    print(f"  status: {status}")
    listed: list[str] = []
    if status == 200:
        data = models_body.get("data") or []
        if isinstance(data, list):
            listed = [
                m.get("id") for m in data if isinstance(m, dict) and m.get("id")
            ]
        print(f"  count: {len(listed)}")
        print(f"  sample: {listed[:8]}")
    else:
        print(f"  error: {json.dumps(models_body)[:500]}")
        return 1

    model = pick_model(listed, args.model)
    print(f"\n=== POST /v1/chat/completions model={model} ===")
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Reply in one short sentence."},
            {
                "role": "user",
                "content": "Say hello and confirm you are Grok answering via the xAI API.",
            },
        ],
        "max_tokens": 80,
        "temperature": 0.2,
    }
    status, resp = http_json(
        "POST", f"{API_BASE}/chat/completions", token, body, timeout=120.0
    )
    print(f"  status: {status}")
    if status != 200:
        print(f"  error: {json.dumps(resp)[:800]}")
        return 1

    content = None
    choices = resp.get("choices") or []
    if choices and isinstance(choices[0], dict):
        content = (choices[0].get("message") or {}).get("content")
    print(f"  usage: {resp.get('usage')}")
    print(f"  assistant: {content!r}")
    print("\nOK: Grok Build auth.json session token works for xAI inference.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
