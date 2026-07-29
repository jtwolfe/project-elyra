"""Hermetic tests for docs/lance-debug1 api_matrix probe (no live operator data).

Skips when lancedb is missing or connect is unusable (e.g. segfault on some
Python builds). Does not import or open LanceMemoryStore.
"""

from __future__ import annotations

import json
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import pytest

pytest.importorskip("lancedb")

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "docs" / "lance-debug1" / "scripts"
API_MATRIX = SCRIPTS / "api_matrix.py"
BUILD_FIXTURE = SCRIPTS / "fixtures" / "build_tiny_atoms.py"
ENV_CHECK = SCRIPTS / "env_check.py"
QUARANTINE = SCRIPTS / "quarantine_copy.sh"


@lru_cache(maxsize=1)
def _lancedb_connect_works() -> bool:
    """Probe connect in a subprocess so a segfault cannot kill pytest."""
    code = (
        "import tempfile, lancedb\n"
        "d = tempfile.mkdtemp()\n"
        "db = lancedb.connect(d)\n"
        "db.table_names()\n"
        "print('ok')\n"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and "ok" in (proc.stdout or "")


pytestmark = pytest.mark.skipif(
    not _lancedb_connect_works(),
    reason="lancedb import ok but connect unusable on this Python/runtime",
)


def test_env_check_runs():
    proc = subprocess.run(
        [sys.executable, str(ENV_CHECK), "--json"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert "packages" in data
    assert "python" in data


def test_api_matrix_on_tiny_fixture(tmp_path: Path):
    uri = tmp_path / "tiny-lance"
    build = subprocess.run(
        [sys.executable, str(BUILD_FIXTURE), "--out", str(uri), "--rows", "25"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert build.returncode == 0, build.stderr + build.stdout
    assert uri.is_dir()

    out = tmp_path / "api-matrix.json"
    probe = subprocess.run(
        [
            sys.executable,
            str(API_MATRIX),
            "--uri",
            str(uri),
            "--out",
            str(out),
            "--quiet",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert probe.returncode == 0, probe.stderr + probe.stdout
    assert out.is_file()
    data = json.loads(out.read_text(encoding="utf-8"))

    assert data["summary"]["n_full"] == 25
    # On lancedb 0.20.x bare to_arrow is default-limit 10
    assert data["summary"]["n_arrow"] == 10
    assert data["h1"]["ok"] is True
    assert data["h1a"]["ok"] is True
    assert data["h1b"]["ok"] is True
    assert data["h1b"]["path"] in {
        "head_n_full",
        "to_lance",
        "query_public",
        "private_async",
    }
    # Chain records attempts until first success (0.20.0 often private_async or head_n_full).
    assert data["h1b"]["attempts"]
    assert "query_public_missing" in data["h1b"]["attempts"] or "query_public" in data[
        "h1b"
    ]["attempts"]
    # Deny-list methods must not appear as call sites; presence may be True.
    assert "deny_list" in data
    for forbidden in ("compact_files", "optimize", "cleanup_old_versions"):
        assert forbidden in data["deny_list"]


def test_quarantine_copy_marker_only(tmp_path: Path):
    """Shell script copies memory root and writes canonical marker only."""
    if not QUARANTINE.is_file():
        pytest.skip("quarantine_copy.sh missing")

    src = tmp_path / "memory"
    (src / "lance").mkdir(parents=True)
    (src / "lance" / "dummy").write_text("x", encoding="utf-8")
    (src / "meta.json").write_text("{}", encoding="utf-8")
    (src / "atoms").mkdir()
    (src / "atoms" / "blob").write_text("b", encoding="utf-8")

    qroot = tmp_path / "qroot"
    proc = subprocess.run(
        ["bash", str(QUARANTINE), str(src), str(qroot)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout

    marker = qroot / ".lance-debug1-quarantine"
    assert marker.is_file()
    body = json.loads(marker.read_text(encoding="utf-8"))
    assert body["kind"] == "lance-debug1-quarantine"
    assert body["possibly_torn"] in (True, False)

    # Destination layout
    assert (qroot / "data" / "memory" / "lance" / "dummy").is_file()
    assert (qroot / "data" / "memory" / "meta.json").is_file()
    assert (qroot / "data" / "memory" / "ladder").is_dir()

    # Non-canonical markers must not exist
    assert not (qroot / "data" / ".lance-debug1-quarantine").exists()
    assert not (qroot / "data" / "memory" / ".lance-debug1-quarantine").exists()
