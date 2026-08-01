"""Unit tests: live auth_provider CLI (PR2 / KD5b)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from elyra.instrument import auth_provider as ap


@dataclass
class _FakeFresh:
    ok: bool
    access_token: str | None
    expires_at: str | None
    detail: str | None = None
    email: str | None = None
    rotated: bool = False


def _iso_in(seconds: float) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_run_provider_success_access_only(tmp_path: Path) -> None:
    fixed_now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
    expires_at = "2026-08-01T13:00:00Z"  # 3600s later

    def fake_ensure(data_dir: Path, **kwargs: Any) -> _FakeFresh:
        assert data_dir == tmp_path
        return _FakeFresh(
            ok=True,
            access_token="tok-access-abc",
            expires_at=expires_at,
        )

    code, stdout, stderr = ap.run_provider(
        tmp_path,
        ensure_fresh=fake_ensure,
        now=fixed_now,
    )
    assert code == 0
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload == {"access_token": "tok-access-abc", "expires_in": 3600}
    assert "refresh_token" not in payload
    assert "refresh" not in stdout


def test_run_provider_expires_in_floor(tmp_path: Path) -> None:
    fixed_now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
    # Only 10s left → clamp to FLOOR_S (60).
    expires_at = "2026-08-01T12:00:10Z"

    def fake_ensure(data_dir: Path, **kwargs: Any) -> _FakeFresh:
        return _FakeFresh(ok=True, access_token="t", expires_at=expires_at)

    code, stdout, _ = ap.run_provider(
        tmp_path, ensure_fresh=fake_ensure, now=fixed_now
    )
    assert code == 0
    assert json.loads(stdout)["expires_in"] == ap.FLOOR_S


def test_run_provider_missing_expires_at_fallback(tmp_path: Path) -> None:
    def fake_ensure(data_dir: Path, **kwargs: Any) -> _FakeFresh:
        return _FakeFresh(ok=True, access_token="t", expires_at=None)

    code, stdout, _ = ap.run_provider(tmp_path, ensure_fresh=fake_ensure)
    assert code == 0
    assert json.loads(stdout)["expires_in"] == ap.DEFAULT_EXPIRES_IN_FALLBACK


def test_run_provider_fail_nonzero(tmp_path: Path) -> None:
    def fake_ensure(data_dir: Path, **kwargs: Any) -> _FakeFresh:
        return _FakeFresh(
            ok=False,
            access_token=None,
            expires_at=None,
            detail="oauth_reauth_required",
        )

    code, stdout, stderr = ap.run_provider(tmp_path, ensure_fresh=fake_ensure)
    assert code != 0
    assert stdout == ""
    assert "auth_unavailable" in stderr
    assert "oauth_reauth_required" in stderr
    assert "refresh" not in stderr.lower() or "refresh_token" not in stderr


def test_grok_auth_expired_forces_refresh(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def fake_ensure(data_dir: Path, **kwargs: Any) -> _FakeFresh:
        seen["force"] = kwargs.get("force")
        seen["data_dir"] = data_dir
        return _FakeFresh(
            ok=True,
            access_token="fresh-after-expire",
            expires_at=_iso_in(3600),
        )

    env = {"GROK_AUTH_EXPIRED": "1"}
    code, stdout, _ = ap.run_provider(
        tmp_path, ensure_fresh=fake_ensure, env=env
    )
    assert code == 0
    assert seen["force"] is True
    assert json.loads(stdout)["access_token"] == "fresh-after-expire"


def test_grok_auth_expired_unset_no_force(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def fake_ensure(data_dir: Path, **kwargs: Any) -> _FakeFresh:
        seen["force"] = kwargs.get("force", "MISSING")
        return _FakeFresh(ok=True, access_token="t", expires_at=_iso_in(3600))

    code, _, _ = ap.run_provider(
        tmp_path, ensure_fresh=fake_ensure, env={}
    )
    assert code == 0
    assert seen["force"] is False


def test_resolve_data_dir_from_elyra_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    resolved = ap.resolve_data_dir(env={"ELYRA_HOME": str(home)})
    assert resolved == (home / "data").resolve()


def test_resolve_data_dir_explicit_wins(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit_data"
    explicit.mkdir()
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    resolved = ap.resolve_data_dir(
        data_dir=explicit,
        env={"ELYRA_HOME": str(home)},
    )
    assert resolved == explicit.resolve()


def test_main_missing_data_dir_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ELYRA_HOME", raising=False)
    monkeypatch.delenv("ELYRA_DATA_DIR", raising=False)
    assert ap.main([]) != 0


def test_main_with_mock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_ensure(data_dir: Path, **kwargs: Any) -> _FakeFresh:
        return _FakeFresh(
            ok=True,
            access_token="main-tok",
            expires_at=_iso_in(7200),
        )

    monkeypatch.setattr(ap, "ensure_fresh_access", fake_ensure)
    code = ap.main(["--data-dir", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    payload = json.loads(out.strip())
    assert payload["access_token"] == "main-tok"
    assert "expires_in" in payload
    assert "refresh_token" not in payload
