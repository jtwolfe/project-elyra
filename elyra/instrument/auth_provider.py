"""Live Grok auth provider CLI — PE OAuth access-only (KD5b).

Scope: stdout JSON ``{access_token, expires_in}`` via ``ensure_fresh_access``;
honor ``GROK_AUTH_EXPIRED``; never print refresh_token; exit non-zero on fail.
Out of scope: interactive login, writing operator ``~/.grok/auth.json``,
subprocess broker, skill seed.

Invoked by Grok as::

    <sys.executable> -m elyra.instrument.auth_provider --data-dir <abs data_dir>

Or with ``ELYRA_HOME`` set (data_dir = ``$ELYRA_HOME/data``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from elyra.llm.xai_oauth import ensure_fresh_access, seconds_until_expiry

# Floor for expires_in so Grok does not treat the token as already expired.
FLOOR_S = 60
# Only if expires_at missing but access ok (should be rare).
DEFAULT_EXPIRES_IN_FALLBACK = 3600


def resolve_data_dir(
    *,
    data_dir: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    """Resolve PE data_dir from explicit arg, ELYRA_DATA_DIR, or ELYRA_HOME."""
    e = env if env is not None else os.environ
    if data_dir is not None and str(data_dir).strip():
        return Path(data_dir).expanduser().resolve()

    raw_data = (e.get("ELYRA_DATA_DIR") or "").strip()
    if raw_data:
        return Path(raw_data).expanduser().resolve()

    raw_home = (e.get("ELYRA_HOME") or "").strip()
    if raw_home:
        from elyra.config import resolve_paths

        return resolve_paths(Path(raw_home).expanduser()).data_dir.resolve()

    raise ValueError(
        "data_dir required: pass --data-dir or set ELYRA_HOME / ELYRA_DATA_DIR"
    )


def expires_in_from_result(
    expires_at: str | None,
    *,
    now=None,
    floor_s: int = FLOOR_S,
    fallback: int = DEFAULT_EXPIRES_IN_FALLBACK,
) -> int:
    """Derive ``expires_in`` seconds from ISO ``expires_at`` (normative)."""
    secs = seconds_until_expiry(expires_at, now=now)
    if secs is None:
        return max(floor_s, int(fallback))
    return max(floor_s, int(secs))


def access_payload(
    access_token: str,
    expires_at: str | None,
    *,
    now=None,
) -> dict[str, object]:
    """Build access-only JSON object (never includes refresh_token)."""
    return {
        "access_token": access_token,
        "expires_in": expires_in_from_result(expires_at, now=now),
    }


def run_provider(
    data_dir: Path | str,
    *,
    force: bool | None = None,
    env: dict[str, str] | None = None,
    ensure_fresh=None,
    now=None,
) -> tuple[int, str, str]:
    """Call ensure_fresh_access and return (exit_code, stdout, stderr).

    When ``GROK_AUTH_EXPIRED=1`` (or ``force=True``), refresh is forced.
    Stdout is a single JSON object on success; never includes refresh_token.
    """
    e = env if env is not None else os.environ
    if force is None:
        force = (e.get("GROK_AUTH_EXPIRED") or "").strip() == "1"

    fn = ensure_fresh if ensure_fresh is not None else ensure_fresh_access
    try:
        fresh = fn(Path(data_dir), force=force)
    except TypeError:
        # Test doubles may not accept force=
        fresh = fn(Path(data_dir))
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        return 1, "", f"auth_provider error: {type(exc).__name__}: {exc}"

    ok = bool(getattr(fresh, "ok", False))
    token = getattr(fresh, "access_token", None)
    if not ok or not token or not isinstance(token, str):
        detail = getattr(fresh, "detail", None) or "auth_unavailable"
        # Status-safe only — never echo tokens.
        return 1, "", f"auth_unavailable: {detail}"

    expires_at = getattr(fresh, "expires_at", None)
    payload = access_payload(token, expires_at, now=now)
    # Belt-and-suspenders: never leak refresh keys if someone expands payload.
    assert "refresh_token" not in payload
    assert "refresh" not in payload
    out = json.dumps(payload, separators=(",", ":"))
    return 0, out, ""


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="elyra.instrument.auth_provider",
        description=(
            "Print PE xai_oauth access-only JSON for Grok auth_provider_command. "
            "Never prints refresh_token."
        ),
    )
    p.add_argument(
        "--data-dir",
        dest="data_dir",
        default=None,
        help="Absolute PE data_dir (oauth store). Else ELYRA_DATA_DIR or ELYRA_HOME/data.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        data_dir = resolve_data_dir(data_dir=args.data_dir)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    code, stdout, stderr = run_provider(data_dir)
    if stderr:
        print(stderr, file=sys.stderr)
    if stdout:
        # One JSON object on stdout — Grok parses this.
        sys.stdout.write(stdout)
        if not stdout.endswith("\n"):
            sys.stdout.write("\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
