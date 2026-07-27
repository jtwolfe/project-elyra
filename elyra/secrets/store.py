"""File-backed named secrets store under ``data/secrets/``.

Layout::

    data/secrets/
      xai_api_key          # reserved — elyra.llm.auth (untouched)
      xai_api_key.tmp
      meta.json            # index (no values)
      values/              # 0700
        <name>             # 0600 raw UTF-8 value

Never log or return secret values except via ``get_value`` (internal inject).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from elyra.identity.layout import utc_now_iso
from elyra.llm.auth import ensure_secrets_dir
from elyra.secrets.policy import (
    MANAGED_BY_USER,
    normalize_grants,
    validate_secret_name,
)

_LOG = logging.getLogger(__name__)

META_FILENAME = "meta.json"
VALUES_DIRNAME = "values"


class SecretsStore:
    """Thread-safe file store for named operator secrets."""

    def __init__(self, data_dir: Path | str) -> None:
        self._data_dir = Path(data_dir)
        self._lock = threading.RLock()
        self.ensure_layout()

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def secrets_dir(self) -> Path:
        return ensure_secrets_dir(self._data_dir)

    @property
    def meta_path(self) -> Path:
        return self.secrets_dir / META_FILENAME

    @property
    def values_dir(self) -> Path:
        return self.secrets_dir / VALUES_DIRNAME

    def ensure_layout(self) -> None:
        """Create secrets dir (0700) + values/ (0700); leave xai_api_key alone."""
        ensure_secrets_dir(self._data_dir)
        values = self.values_dir
        values.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(values, 0o700)
        except OSError:
            pass
        if not self.meta_path.is_file():
            self._write_meta({"secrets": {}})

    def list_secrets(self) -> list[dict[str, Any]]:
        """Return redacted metadata rows (never values)."""
        with self._lock:
            meta = self._load_meta()
            secrets = meta.get("secrets") or {}
            if not isinstance(secrets, dict):
                return []
            rows: list[dict[str, Any]] = []
            for name, entry in sorted(secrets.items(), key=lambda kv: str(kv[0])):
                if not isinstance(name, str) or not isinstance(entry, dict):
                    continue
                rows.append(self._public_entry(name, entry))
            return rows

    def get_meta(self, name: str) -> dict[str, Any] | None:
        """Return public meta for one secret, or None if missing."""
        try:
            key = validate_secret_name(name)
        except ValueError:
            return None
        with self._lock:
            meta = self._load_meta()
            secrets = meta.get("secrets") or {}
            if not isinstance(secrets, dict):
                return None
            entry = secrets.get(key)
            if not isinstance(entry, dict):
                return None
            return self._public_entry(key, entry)

    def get_value(self, name: str) -> str | None:
        """Read secret value for inject only. Returns None if missing/invalid."""
        try:
            key = validate_secret_name(name)
        except ValueError:
            return None
        with self._lock:
            path = self._value_path(key)
            if not path.is_file():
                return None
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                return None
            # Values are stored with optional trailing newline (like api key).
            if text.endswith("\n"):
                text = text[:-1]
            return text if text else None

    def known_values(self) -> list[str]:
        """Return all stored secret values (for result redaction). Internal only."""
        with self._lock:
            meta = self._load_meta()
            secrets = meta.get("secrets") or {}
            if not isinstance(secrets, dict):
                return []
            out: list[str] = []
            for name in secrets:
                if not isinstance(name, str):
                    continue
                try:
                    key = validate_secret_name(name)
                except ValueError:
                    continue
                path = self._value_path(key)
                if not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
                if text.endswith("\n"):
                    text = text[:-1]
                if text:
                    out.append(text)
            return out

    def set_secret(
        self,
        name: str,
        value: str,
        *,
        grants: list[str] | None = None,
        managed_by: str = MANAGED_BY_USER,
    ) -> dict[str, Any]:
        """Write value + meta. Returns public meta (never the value).

        Raises ValueError with status-safe reason codes.
        """
        key = validate_secret_name(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("empty_secret_value")

        grant_list = normalize_grants(grants) if grants is not None else None
        now = utc_now_iso()

        with self._lock:
            meta = self._load_meta()
            secrets = meta.setdefault("secrets", {})
            if not isinstance(secrets, dict):
                secrets = {}
                meta["secrets"] = secrets
            existing = secrets.get(key) if isinstance(secrets.get(key), dict) else None
            created_at = (
                existing.get("created_at")
                if isinstance(existing, dict) and isinstance(existing.get("created_at"), str)
                else now
            )
            last_used = (
                existing.get("last_used_at")
                if isinstance(existing, dict)
                else None
            )
            if grant_list is None:
                if isinstance(existing, dict) and isinstance(existing.get("grants"), list):
                    grant_list = normalize_grants(existing.get("grants"))
                else:
                    grant_list = []
            entry = {
                "managed_by": managed_by if managed_by else MANAGED_BY_USER,
                "created_at": created_at,
                "updated_at": now,
                "last_used_at": last_used,
                "grants": grant_list,
            }
            self._write_value(key, value)
            secrets[key] = entry
            self._write_meta(meta)
            return self._public_entry(key, entry)

    def delete_secret(self, name: str) -> bool:
        """Delete secret value + meta entry. Returns True if it existed."""
        try:
            key = validate_secret_name(name)
        except ValueError as exc:
            raise ValueError(str(exc) or "invalid_secret_name") from exc
        with self._lock:
            meta = self._load_meta()
            secrets = meta.get("secrets") or {}
            if not isinstance(secrets, dict):
                secrets = {}
            existed = key in secrets
            path = self._value_path(key)
            if path.is_file():
                try:
                    path.unlink()
                    existed = True
                except OSError:
                    pass
            # Clean leftover tmp for this name if any.
            tmp = path.with_suffix(path.suffix + ".tmp") if path.suffix else Path(str(path) + ".tmp")
            # Atomic write uses name.tmp pattern under values/
            tmp2 = self.values_dir / f"{key}.tmp"
            for t in (tmp, tmp2):
                try:
                    t.unlink(missing_ok=True)
                except OSError:
                    pass
            if key in secrets:
                del secrets[key]
                meta["secrets"] = secrets
                self._write_meta(meta)
                existed = True
            return existed

    def set_grants(self, name: str, grants: list[str] | Any) -> dict[str, Any]:
        """Replace grants list for an existing secret. Returns public meta."""
        key = validate_secret_name(name)
        grant_list = normalize_grants(grants)
        with self._lock:
            meta = self._load_meta()
            secrets = meta.get("secrets") or {}
            if not isinstance(secrets, dict) or key not in secrets:
                raise ValueError("secret_not_found")
            entry = secrets[key]
            if not isinstance(entry, dict):
                raise ValueError("secret_not_found")
            entry = dict(entry)
            entry["grants"] = grant_list
            entry["updated_at"] = utc_now_iso()
            secrets[key] = entry
            meta["secrets"] = secrets
            self._write_meta(meta)
            return self._public_entry(key, entry)

    def touch_last_used(self, name: str) -> None:
        """Best-effort update last_used_at after successful inject."""
        try:
            key = validate_secret_name(name)
        except ValueError:
            return
        with self._lock:
            meta = self._load_meta()
            secrets = meta.get("secrets") or {}
            if not isinstance(secrets, dict):
                return
            entry = secrets.get(key)
            if not isinstance(entry, dict):
                return
            entry = dict(entry)
            entry["last_used_at"] = utc_now_iso()
            secrets[key] = entry
            meta["secrets"] = secrets
            try:
                self._write_meta(meta)
            except OSError as exc:
                _LOG.warning("secrets touch_last_used failed: %s", exc)

    # ── internals ────────────────────────────────────────────────────────

    def _value_path(self, name: str) -> Path:
        return self.values_dir / name

    def _public_entry(self, name: str, entry: dict[str, Any]) -> dict[str, Any]:
        grants = entry.get("grants") if isinstance(entry.get("grants"), list) else []
        clean_grants = [g for g in grants if isinstance(g, str)]
        return {
            "name": name,
            "managed_by": entry.get("managed_by") or MANAGED_BY_USER,
            "created_at": entry.get("created_at"),
            "updated_at": entry.get("updated_at"),
            "last_used_at": entry.get("last_used_at"),
            "grants": clean_grants,
        }

    def _load_meta(self) -> dict[str, Any]:
        path = self.meta_path
        if not path.is_file():
            return {"secrets": {}}
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
        except (OSError, UnicodeError, json.JSONDecodeError):
            _LOG.warning("secrets meta.json unreadable; treating as empty")
            return {"secrets": {}}
        if not isinstance(data, dict):
            return {"secrets": {}}
        if "secrets" not in data or not isinstance(data.get("secrets"), dict):
            data = dict(data)
            data["secrets"] = {}
        return data

    def _write_meta(self, meta: dict[str, Any]) -> None:
        path = self.meta_path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        text = json.dumps(meta, ensure_ascii=False, indent=2) + "\n"
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            fd = os.open(str(tmp), flags, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _write_value(self, name: str, value: str) -> None:
        """Atomic write value file mode 0600."""
        self.values_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self.values_dir, 0o700)
        except OSError:
            pass
        final = self._value_path(name)
        tmp = self.values_dir / f"{name}.tmp"
        payload = value + "\n"
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_EXCL"):
            try:
                fd = os.open(str(tmp), flags | os.O_EXCL, 0o600)
            except FileExistsError:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                fd = os.open(str(tmp), flags | os.O_EXCL, 0o600)
        else:
            fd = os.open(str(tmp), flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, final)
            try:
                os.chmod(final, 0o600)
            except OSError:
                pass
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise


__all__ = ["META_FILENAME", "VALUES_DIRNAME", "SecretsStore"]
