#!/usr/bin/env python3
"""Stage live-eval runner — product path (presence + API + real Gemma).

Scope: fixed scenarios, isolated ELYRA_HOME per attempt, POST message, poll
close/timeout, export tape/messages, fill scorecard via reasoning_hygiene.
In scope: Stage 0 baseline orchestration; reuse healthy llama or start one.
Out of scope: product sampling default changes; do-loop-only non-gating mode
as stage gate (supported only for debug via --score-only).

Usage:
  python scripts/live_eval/run_stage.py --stage 0 --all-scenarios --tries 3
  python scripts/live_eval/run_stage.py --stage 0 --scenario S-social --try 1
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Repo root on sys.path when invoked as scripts/live_eval/run_stage.py
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from elyra.config import ElyraPaths, project_root, resolve_paths  # noqa: E402
from elyra.llm.client import GatedChatClient, HttpChatClient  # noqa: E402
from elyra.llm.config import LlamaServerConfig  # noqa: E402
from elyra.llm.queue import LlamaServerGate  # noqa: E402
from elyra.llm.reasoning_hygiene import (  # noqa: E402
    channel_marker_count,
    is_channel_flood,
    strip_channel_markers,
)
from elyra.llm.server import build_server_command, validate_model_paths  # noqa: E402
from elyra.presence.worker import PresenceWorker  # noqa: E402
from elyra.runtime.api import start_api_server  # noqa: E402
from elyra.runtime.config import RuntimeConfig  # noqa: E402
from elyra.runtime.state import RuntimeState, set_runtime_state  # noqa: E402
from elyra.settings import load_settings  # noqa: E402

_LOG = logging.getLogger("live_eval")

DEFAULT_TRIES = 3
SNIPPET_CHARS = 1200


# ---------------------------------------------------------------------------
# Scenarios (stdlib YAML-ish load — no PyYAML dependency)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    id: str
    intent: str
    prompt: str
    expects_tools: bool = True
    expects_speak: bool = True
    expects_no_flood: bool = True


@dataclass(frozen=True)
class StageConfig:
    stage: int
    name: str
    temperature: float
    top_p: Any
    top_k: Any
    reasoning_budget_tokens: Any
    max_tool_hops: int
    moment_wall_clock_minutes: int
    poll_timeout_seconds: float
    scenarios: tuple[Scenario, ...]


def _parse_scalar(raw: str) -> Any:
    s = raw.strip()
    if s in ("null", "Null", "NULL", "~"):
        return None
    if s in ("true", "True"):
        return True
    if s in ("false", "False"):
        return False
    if (s.startswith('"') and s.endswith('"')) or (
        s.startswith("'") and s.endswith("'")
    ):
        return s[1:-1]
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return s


def load_scenarios(path: Path | None = None) -> StageConfig:
    """Load scenarios.yaml with a minimal subset parser (no PyYAML)."""
    path = path or (_HERE / "scenarios.yaml")
    text = path.read_text(encoding="utf-8")
    knobs: dict[str, Any] = {
        "temperature": 0.2,
        "top_p": None,
        "top_k": None,
        "reasoning_budget_tokens": None,
    }
    caps: dict[str, Any] = {
        "max_tool_hops": 12,
        "moment_wall_clock_minutes": 10,
        "poll_timeout_seconds": 620,
    }
    stage = 0
    name = "baseline"
    scenarios: list[Scenario] = []
    section: str | None = None
    cur: dict[str, Any] | None = None
    cur_expects: dict[str, Any] = {}

    def _flush() -> None:
        nonlocal cur, cur_expects
        if cur and cur.get("id") and cur.get("prompt") is not None:
            scenarios.append(
                Scenario(
                    id=str(cur["id"]),
                    intent=str(cur.get("intent") or ""),
                    prompt=str(cur["prompt"]),
                    expects_tools=bool(cur_expects.get("tools", True)),
                    expects_speak=bool(cur_expects.get("speak", True)),
                    expects_no_flood=not bool(
                        cur_expects.get("flood", False)
                    ),  # flood: false → expect no flood
                )
            )
        cur = None
        cur_expects = {}

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if indent == 0 and stripped.endswith(":") and not stripped.startswith("-"):
            _flush()
            key = stripped[:-1].strip()
            if key == "scenarios":
                section = "scenarios"
            elif key == "knobs":
                section = "knobs"
            elif key == "eval_caps":
                section = "eval_caps"
            else:
                section = None
            continue

        if indent == 0 and ":" in stripped and not stripped.startswith("-"):
            key, _, val = stripped.partition(":")
            key, val = key.strip(), val.strip()
            if key == "stage" and val:
                stage = int(val)
            elif key == "name" and val:
                name = _parse_scalar(val) if val else name
            continue

        if section in ("knobs", "eval_caps") and ":" in stripped:
            key, _, val = stripped.partition(":")
            key, val = key.strip(), val.strip()
            target = knobs if section == "knobs" else caps
            if val:
                target[key] = _parse_scalar(val)
            continue

        if section == "scenarios":
            if stripped.startswith("- "):
                _flush()
                cur = {}
                cur_expects = {}
                rest = stripped[2:].strip()
                if ":" in rest:
                    k, _, v = rest.partition(":")
                    cur[k.strip()] = _parse_scalar(v.strip()) if v.strip() else None
                continue
            if cur is None:
                continue
            if stripped == "expects:":
                continue
            if ":" in stripped:
                k, _, v = stripped.partition(":")
                k, v = k.strip(), v.strip()
                if k in ("tools", "speak", "flood"):
                    cur_expects[k] = _parse_scalar(v) if v else None
                else:
                    cur[k] = _parse_scalar(v) if v else ""
    _flush()

    if not scenarios:
        raise SystemExit(f"no scenarios loaded from {path}")

    return StageConfig(
        stage=stage,
        name=str(name),
        temperature=float(knobs.get("temperature", 0.2)),
        top_p=knobs.get("top_p"),
        top_k=knobs.get("top_k"),
        reasoning_budget_tokens=knobs.get("reasoning_budget_tokens"),
        max_tool_hops=int(caps.get("max_tool_hops", 12)),
        moment_wall_clock_minutes=int(caps.get("moment_wall_clock_minutes", 10)),
        poll_timeout_seconds=float(caps.get("poll_timeout_seconds", 620)),
        scenarios=tuple(scenarios),
    )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _http_json(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"error": raw}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, {"error": str(exc)}


def _server_healthy(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Llama lifecycle
# ---------------------------------------------------------------------------


@dataclass
class LlamaHandle:
    config: LlamaServerConfig
    proc: subprocess.Popen[bytes] | None = None
    owned: bool = False

    def stop(self) -> None:
        if not self.owned or self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        self.proc = None


def ensure_llama(
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    context_tokens: int | None = None,
    start_if_needed: bool = True,
    health_timeout: float = 300.0,
) -> LlamaHandle:
    """Reuse healthy server or start one from project model/."""
    paths = resolve_paths(_ROOT)
    problems = validate_model_paths(paths)
    if problems:
        raise SystemExit("model not available: " + "; ".join(problems))

    cfg = LlamaServerConfig(host=host, port=port)
    if _server_healthy(cfg.health_url):
        _LOG.info("reusing healthy llama-server at %s:%s", host, port)
        return LlamaHandle(config=cfg, proc=None, owned=False)

    if not start_if_needed:
        raise SystemExit(
            f"llama-server not healthy at {cfg.health_url} "
            f"(pass --start-llama or start it yourself)"
        )

    # If default port busy but unhealthy, pick a free port.
    if port == 8080:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                if s.connect_ex((host, port)) == 0:
                    port = _free_port()
                    cfg = LlamaServerConfig(host=host, port=port)
                    _LOG.warning("port 8080 busy/unhealthy — starting on %s", port)
        except OSError:
            pass

    ctx = context_tokens or 86000
    # Prefer slightly smaller ctx if operator sets LIVE_EVAL_CTX
    env_ctx = os.environ.get("LIVE_EVAL_CTX", "").strip()
    if env_ctx.isdigit():
        ctx = int(env_ctx)

    cmd = build_server_command(
        paths,
        cfg,
        context_tokens=ctx,
    )
    _LOG.info("starting llama-server: %s …", " ".join(cmd[:6]))
    log_path = _HERE / "logs" / "llama-server-live-eval.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = open(log_path, "ab")  # noqa: SIM115
    proc = subprocess.Popen(
        cmd,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        cwd=str(paths.home),
    )
    deadline = time.monotonic() + health_timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log_f.close()
            tail = ""
            try:
                tail = log_path.read_bytes()[-2000:].decode("utf-8", errors="replace")
            except OSError:
                pass
            raise SystemExit(
                f"llama-server exited early (code {proc.returncode}): {tail}"
            )
        if _server_healthy(cfg.health_url, timeout=1.0):
            _LOG.info("llama-server ready on %s:%s", cfg.host, cfg.port)
            return LlamaHandle(config=cfg, proc=proc, owned=True)
        time.sleep(1.0)
    proc.terminate()
    log_f.close()
    raise SystemExit(f"llama-server health timeout after {health_timeout}s")


# ---------------------------------------------------------------------------
# Isolated home + product stack
# ---------------------------------------------------------------------------


def prepare_attempt_home(
    attempt_id: str,
    *,
    max_tool_hops: int,
    moment_wall_clock_minutes: int,
    runs_root: Path | None = None,
) -> Path:
    """Unique ELYRA_HOME with model/skills/tools/prompts linked + eval toml."""
    runs_root = runs_root or (_HERE / "logs" / "runs")
    home = runs_root / attempt_id / "home"
    if home.exists():
        shutil.rmtree(home)
    home.mkdir(parents=True)

    project = project_root()
    for name in ("model", "skills", "tools", "prompts"):
        src = project / name
        dest = home / name
        if src.exists():
            dest.symlink_to(src.resolve())
        elif name == "model":
            raise SystemExit(f"project model/ missing at {src}")

    toml = home / "elyra.toml"
    toml.write_text(
        "\n".join(
            [
                "# Live-eval attempt home — product defaults + eval caps",
                "[loop]",
                f"max_tool_hops = {int(max_tool_hops)}",
                f"moment_wall_clock_minutes = {int(moment_wall_clock_minutes)}",
                # generation_max_tokens stays product default (8192)
                "",
            ]
        ),
        encoding="utf-8",
    )
    return home


@dataclass
class ProductStack:
    paths: ElyraPaths
    base_url: str
    stop: threading.Event
    worker: PresenceWorker
    worker_thread: threading.Thread
    api_server: Any
    api_thread: threading.Thread
    gate: LlamaServerGate
    state: RuntimeState

    def shutdown(self) -> None:
        self.stop.set()
        self.gate.shutdown()
        try:
            self.api_server.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.api_server.server_close()
        except Exception:  # noqa: BLE001
            pass
        self.worker_thread.join(timeout=5.0)
        self.api_thread.join(timeout=5.0)


def start_product_stack(
    home: Path,
    llama: LlamaServerConfig,
    *,
    api_port: int | None = None,
) -> ProductStack:
    """PresenceWorker + API wired like elyra start (external llama OK)."""
    paths = resolve_paths(home)
    paths.ensure_data_dirs()
    settings = load_settings(paths.home)

    stop = threading.Event()
    gate = LlamaServerGate()
    state = RuntimeState()
    set_runtime_state(state)
    state.set_llama(pid=None, ready=True)

    client = GatedChatClient(HttpChatClient(llama), gate)
    worker = PresenceWorker(
        paths=paths,
        client=client,
        stop_event=stop,
        settings=settings,
    )
    worker_thread = threading.Thread(
        target=worker.run,
        name="live-eval-presence",
        daemon=True,
    )
    worker_thread.start()

    port = 0 if api_port is None else api_port
    config = RuntimeConfig(
        api_host="127.0.0.1",
        api_port=port,
        start_llama_server=False,
        llama=llama,
    )
    api_server, api_thread = start_api_server(
        config,
        paths=paths,
        gate=gate,
        state=state,
        worker=worker,
    )
    host, bound_port = api_server.server_address[:2]
    base = f"http://{host}:{bound_port}"
    # Wait for health
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        code, _ = _http_json("GET", f"{base}/api/health", timeout=2.0)
        if code == 200:
            break
        time.sleep(0.1)
    else:
        stop.set()
        raise RuntimeError(f"API failed to become healthy at {base}")

    return ProductStack(
        paths=paths,
        base_url=base,
        stop=stop,
        worker=worker,
        worker_thread=worker_thread,
        api_server=api_server,
        api_thread=api_thread,
        gate=gate,
        state=state,
    )


# ---------------------------------------------------------------------------
# Run one attempt
# ---------------------------------------------------------------------------


@dataclass
class AttemptResult:
    attempt_id: str
    scenario_id: str
    try_n: int
    status: str  # ok | infra_timeout | infra_error | post_failed
    started_at: str
    ended_at: str
    latency_s: float
    moment_id: str = ""
    hop_count: int = 0
    stop_reason: str = ""
    spoke: bool = False
    tools: list[str] = field(default_factory=list)
    per_hop_finish_reason: list[str] = field(default_factory=list)
    reasoning_len: int = 0
    markers_content: int = 0
    markers_reasoning: int = 0
    markers_strip_c: int = 0
    markers_strip_r: int = 0
    flood: bool = False
    glass_speak: bool = False
    free_text_only: bool = False
    feel: int = 0
    notes: str = ""
    content_snippet: str = ""
    reasoning_snippet: str = ""
    glass_snippet: str = ""
    tape_path: str = ""
    messages_path: str = ""
    export_dir: str = ""
    elyra_home: str = ""
    error: str = ""
    raw_meta: dict[str, Any] = field(default_factory=dict)
    raw_beats: list[dict[str, Any]] = field(default_factory=list)
    raw_messages: list[dict[str, Any]] = field(default_factory=list)

    # rubric
    dim_flood: str = ""
    dim_tools: str = ""
    dim_speak: str = ""
    dim_feel: str = ""
    dim_freetext: str = ""
    dim_flood_notes: str = ""
    dim_tools_notes: str = ""
    dim_speak_notes: str = ""
    dim_feel_notes: str = ""
    dim_freetext_notes: str = ""


def _truncate(text: str, n: int = SNIPPET_CHARS) -> str:
    text = text or ""
    if len(text) <= n:
        return text
    return text[: n - 20] + "\n… [truncated] …\n" + text[-20:]


def score_from_export(
    *,
    attempt_id: str,
    scenario_id: str,
    try_n: int,
    export_dir: Path,
    latency_s: float = 0.0,
    started_at: str = "",
    ended_at: str = "",
    status: str = "ok",
    expects_tools: bool = True,
    expects_speak: bool = True,
) -> AttemptResult:
    """Fill scorecard fields from exported tape + messages."""
    meta_path = export_dir / "moment.json"
    beats_path = export_dir / "beats.jsonl"
    messages_path = export_dir / "messages.jsonl"
    meta: dict[str, Any] = {}
    beats: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []

    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if beats_path.is_file():
        for line in beats_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                beats.append(json.loads(line))
    if messages_path.is_file():
        for line in messages_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                messages.append(json.loads(line))

    moment_id = str(meta.get("id") or "")
    hop_count = int(meta.get("hop_count") or 0)
    stop_reason = str(meta.get("stop_reason") or "")
    stop_beats = [b for b in beats if b.get("type") == "stop"]
    spoke = False
    if stop_beats:
        spoke = bool(stop_beats[-1].get("spoke"))
        stop_reason = str(stop_beats[-1].get("stop_reason") or stop_reason)
        if stop_beats[-1].get("hop_count") is not None:
            hop_count = int(stop_beats[-1]["hop_count"])

    tool_beats = [b for b in beats if b.get("type") == "tool"]
    tools = [str(b.get("name") or "?") for b in tool_beats]

    model_beats = [b for b in beats if b.get("type") == "model"]
    finish_reasons: list[str] = []
    contents: list[str] = []
    reasonings: list[str] = []
    any_tool_calls = False
    free_text_only = False
    for b in model_beats:
        fr = b.get("finish_reason")
        finish_reasons.append(str(fr) if fr is not None else "not_on_tape")
        c = str(b.get("content") or "")
        r = str(b.get("reasoning") or "")
        contents.append(c)
        reasonings.append(r)
        tcs = b.get("tool_calls") or []
        if tcs:
            any_tool_calls = True
        elif c.strip():
            free_text_only = True
    # free_text_only: true if any hop had content+no tools AND no hop had tools
    if any_tool_calls:
        # still flag if ALL hops with content lacked tools? Design: fail if
        # content looks like tool plan AND tool_calls empty when tools required.
        # We mark free_text_only when there was content-without-tools and never
        # structured tool_calls across the moment.
        free_text_only = not any_tool_calls and any(c.strip() for c in contents)
    else:
        free_text_only = any(c.strip() for c in contents)

    all_content = "\n".join(contents)
    all_reasoning = "\n".join(reasonings)
    mc = channel_marker_count(all_content)
    mr = channel_marker_count(all_reasoning)
    sc = channel_marker_count(strip_channel_markers(all_content)) if mc else 0
    sr = channel_marker_count(strip_channel_markers(all_reasoning)) if mr else 0
    flood = is_channel_flood(all_content) or is_channel_flood(all_reasoning)

    glass_assistant = [
        m for m in messages if m.get("role") == "assistant" and str(m.get("content") or "").strip()
    ]
    glass_speak = bool(glass_assistant) or spoke

    # Seed feel 1–5 (operator can edit scorecard)
    feel = 3
    if status == "infra_timeout":
        feel = 1
    elif flood:
        feel = 1
    elif free_text_only and expects_tools:
        feel = 2
    elif expects_speak and not glass_speak:
        feel = 2
    elif tools and glass_speak and not flood:
        feel = 4
        if latency_s and latency_s < 60:
            feel = 5
    elif tools and not flood:
        feel = 3

    notes_parts: list[str] = []
    if status != "ok":
        notes_parts.append(f"status={status}")
    if flood:
        notes_parts.append(f"flood markers c={mc} r={mr}")
    if free_text_only:
        notes_parts.append("free-text without structured tool_calls")
    if not finish_reasons or all(f == "not_on_tape" for f in finish_reasons):
        notes_parts.append(
            "finish_reason not on model beats yet (pre Stage 3 tape field)"
        )

    result = AttemptResult(
        attempt_id=attempt_id,
        scenario_id=scenario_id,
        try_n=try_n,
        status=status,
        started_at=started_at or "",
        ended_at=ended_at or "",
        latency_s=round(latency_s, 2),
        moment_id=moment_id,
        hop_count=hop_count,
        stop_reason=stop_reason or ("timeout" if status == "infra_timeout" else ""),
        spoke=spoke,
        tools=tools,
        per_hop_finish_reason=finish_reasons,
        reasoning_len=sum(len(r) for r in reasonings),
        markers_content=mc,
        markers_reasoning=mr,
        markers_strip_c=sc,
        markers_strip_r=sr,
        flood=flood,
        glass_speak=glass_speak,
        free_text_only=free_text_only,
        feel=feel,
        notes="; ".join(notes_parts),
        content_snippet=_truncate(all_content),
        reasoning_snippet=_truncate(all_reasoning),
        glass_snippet=_truncate(
            "\n---\n".join(str(m.get("content") or "") for m in glass_assistant)
        ),
        tape_path=str(export_dir / "beats.jsonl"),
        messages_path=str(messages_path),
        export_dir=str(export_dir),
        raw_meta=meta,
        raw_beats=beats,
        raw_messages=messages,
    )

    # Rubric dimensions
    result.dim_flood = "PASS" if not flood else "FAIL"
    result.dim_flood_notes = (
        f"markers c={mc} r={mr}" if (mc or mr or flood) else "no markers"
    )

    if expects_tools:
        result.dim_tools = "PASS" if tools else "FAIL"
        result.dim_tools_notes = ",".join(tools) if tools else "no tool beats"
    else:
        result.dim_tools = "N/A"
        result.dim_tools_notes = "tools not required"

    if expects_speak:
        result.dim_speak = "PASS" if glass_speak else "FAIL"
        result.dim_speak_notes = (
            f"spoke={spoke} glass_rows={len(glass_assistant)}"
        )
    else:
        result.dim_speak = "N/A"
        result.dim_speak_notes = "speak not required"

    result.dim_feel = "PASS" if feel >= 3 else "SOFT" if feel == 2 else "FAIL"
    result.dim_feel_notes = f"seeded feel={feel} latency={result.latency_s}s"

    if expects_tools:
        result.dim_freetext = "FAIL" if free_text_only else "PASS"
        result.dim_freetext_notes = (
            "content without tool_calls" if free_text_only else "ok"
        )
    else:
        result.dim_freetext = "N/A"
        result.dim_freetext_notes = ""

    if status.startswith("infra"):
        for attr in (
            "dim_flood",
            "dim_tools",
            "dim_speak",
            "dim_feel",
            "dim_freetext",
        ):
            if getattr(result, attr) not in ("N/A",):
                setattr(result, attr, "INFRA")
        result.dim_feel_notes = status

    return result


def export_attempt(
    stack: ProductStack,
    export_dir: Path,
    moment_id: str | None,
) -> None:
    export_dir.mkdir(parents=True, exist_ok=True)
    moments = stack.worker._moments  # noqa: SLF001 — eval export
    if moment_id:
        meta = moments.get_moment(moment_id)
        beats = moments.list_beats(moment_id)
        (export_dir / "moment.json").write_text(
            json.dumps(meta or {"id": moment_id}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        with (export_dir / "beats.jsonl").open("w", encoding="utf-8") as fh:
            for b in beats:
                fh.write(json.dumps(b, ensure_ascii=False) + "\n")
        # also copy raw tape if present
        try:
            tape = moments.tape_path(moment_id)
            if tape.is_file():
                shutil.copy2(tape, export_dir / f"tape-{moment_id}.jsonl")
        except (ValueError, OSError):
            pass
    else:
        # dump all moments
        all_m = moments.list_moments(limit=20)
        (export_dir / "moments_index.json").write_text(
            json.dumps(all_m, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if all_m:
            mid = all_m[0].get("id")
            if mid:
                return export_attempt(stack, export_dir, str(mid))

    msgs_src = stack.paths.data_dir / "messages.jsonl"
    if msgs_src.is_file():
        shutil.copy2(msgs_src, export_dir / "messages.jsonl")
    else:
        (export_dir / "messages.jsonl").write_text("", encoding="utf-8")


def run_attempt(
    scenario: Scenario,
    *,
    stage: int,
    try_n: int,
    tries: int,
    stage_cfg: StageConfig,
    llama: LlamaHandle,
) -> AttemptResult:
    attempt_id = f"stage-{stage}_{scenario.id}_try-{try_n}"
    started_at = _now_iso()
    t0 = time.monotonic()
    export_dir = _HERE / "logs" / "runs" / attempt_id
    export_dir.mkdir(parents=True, exist_ok=True)

    home = prepare_attempt_home(
        attempt_id,
        max_tool_hops=stage_cfg.max_tool_hops,
        moment_wall_clock_minutes=stage_cfg.moment_wall_clock_minutes,
    )
    stack: ProductStack | None = None
    status = "ok"
    error = ""
    moment_id: str | None = None

    try:
        stack = start_product_stack(home, llama.config)
        _LOG.info(
            "[%s] API %s home=%s prompt=%r",
            attempt_id,
            stack.base_url,
            home,
            scenario.prompt[:80],
        )

        # Snapshot open moments before post
        code, before = _http_json("GET", f"{stack.base_url}/api/moments?limit=5")
        before_ids = {
            m.get("id")
            for m in (before.get("moments") or [])
            if isinstance(m, dict)
        }

        code, post_body = _http_json(
            "POST",
            f"{stack.base_url}/api/messages",
            {"content": scenario.prompt, "user_id": "operator"},
            timeout=30.0,
        )
        if code != 200 or not post_body.get("ok", True):
            status = "post_failed"
            error = f"POST /api/messages -> {code} {post_body}"
            _LOG.error("%s", error)
        else:
            _LOG.info("[%s] enqueued: %s", attempt_id, post_body)

            deadline = time.monotonic() + stage_cfg.poll_timeout_seconds
            closed_id: str | None = None
            while time.monotonic() < deadline:
                # Prefer worker phase idle + a closed moment not in before
                sc, status_body = _http_json(
                    "GET", f"{stack.base_url}/api/status", timeout=5.0
                )
                mc, moments_body = _http_json(
                    "GET", f"{stack.base_url}/api/moments?limit=10", timeout=5.0
                )
                phase = ""
                if sc == 200 and isinstance(status_body, dict):
                    phase = str(status_body.get("phase") or "")
                    active = status_body.get("active_moment_id")
                    if active:
                        moment_id = str(active)

                if mc == 200 and isinstance(moments_body, dict):
                    for m in moments_body.get("moments") or []:
                        if not isinstance(m, dict):
                            continue
                        mid = m.get("id")
                        if mid in before_ids:
                            # same home is fresh — before_ids usually empty
                            pass
                        ended = m.get("ended_at")
                        if mid and ended:
                            closed_id = str(mid)
                            moment_id = closed_id
                            break
                        if mid and not ended:
                            moment_id = str(mid)

                if closed_id and phase in ("idle", "", "waiting"):
                    break
                if closed_id and phase == "idle":
                    break
                # Closed moment is enough even if phase lags
                if closed_id:
                    # give a tick for phase
                    time.sleep(0.5)
                    sc2, st2 = _http_json(
                        "GET", f"{stack.base_url}/api/status", timeout=5.0
                    )
                    if sc2 == 200 and str(st2.get("phase") or "") != "in_moment":
                        break
                    if sc2 == 200 and not st2.get("busy"):
                        break

                time.sleep(1.0)
            else:
                status = "infra_timeout"
                error = (
                    f"moment not closed within {stage_cfg.poll_timeout_seconds}s "
                    f"(last moment_id={moment_id})"
                )
                _LOG.warning("[%s] %s", attempt_id, error)

            if closed_id:
                moment_id = closed_id

        export_attempt(stack, export_dir, moment_id)
    except Exception as exc:  # noqa: BLE001
        status = "infra_error"
        error = f"{type(exc).__name__}: {exc}"
        _LOG.exception("[%s] %s", attempt_id, error)
        if stack is not None:
            try:
                export_attempt(stack, export_dir, moment_id)
            except Exception:  # noqa: BLE001
                pass
    finally:
        if stack is not None:
            stack.shutdown()

    ended_at = _now_iso()
    latency_s = time.monotonic() - t0

    # If export has no moment.json but home has index, try local read
    if not (export_dir / "moment.json").is_file():
        moments_dir = home / "data" / "moments"
        index = moments_dir / "index.jsonl"
        if index.is_file():
            lines = [ln for ln in index.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if lines:
                last = json.loads(lines[-1])
                mid = last.get("id")
                if mid:
                    (export_dir / "moment.json").write_text(
                        json.dumps(last, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                    tape = moments_dir / f"{mid}.jsonl"
                    if tape.is_file():
                        shutil.copy2(tape, export_dir / "beats.jsonl")
        msgs = home / "data" / "messages.jsonl"
        if msgs.is_file() and not (export_dir / "messages.jsonl").is_file():
            shutil.copy2(msgs, export_dir / "messages.jsonl")

    result = score_from_export(
        attempt_id=attempt_id,
        scenario_id=scenario.id,
        try_n=try_n,
        export_dir=export_dir,
        latency_s=latency_s,
        started_at=started_at,
        ended_at=ended_at,
        status=status,
        expects_tools=scenario.expects_tools,
        expects_speak=scenario.expects_speak,
    )
    result.elyra_home = str(home)
    if error:
        result.error = error
        result.notes = (result.notes + "; " if result.notes else "") + error

    # Persist machine-readable result
    (export_dir / "result.json").write_text(
        json.dumps(
            {
                "attempt_id": result.attempt_id,
                "status": result.status,
                "moment_id": result.moment_id,
                "hop_count": result.hop_count,
                "stop_reason": result.stop_reason,
                "spoke": result.spoke,
                "tools": result.tools,
                "flood": result.flood,
                "glass_speak": result.glass_speak,
                "free_text_only": result.free_text_only,
                "latency_s": result.latency_s,
                "markers_content": result.markers_content,
                "markers_reasoning": result.markers_reasoning,
                "feel": result.feel,
                "dims": {
                    "flood": result.dim_flood,
                    "tools": result.dim_tools,
                    "speak": result.dim_speak,
                    "feel": result.dim_feel,
                    "freetext": result.dim_freetext,
                },
                "error": result.error,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return result


# ---------------------------------------------------------------------------
# Scorecard render (simple {{ var }} — no Jinja dependency)
# ---------------------------------------------------------------------------


def render_scorecard(
    result: AttemptResult,
    *,
    stage: int,
    tries: int,
    stage_cfg: StageConfig,
    prompt: str,
    template_path: Path | None = None,
) -> str:
    template_path = template_path or (_HERE / "scorecard.md.j2")
    tpl = template_path.read_text(encoding="utf-8")
    mapping = {
        "stage": str(stage),
        "scenario_id": result.scenario_id,
        "try_n": str(result.try_n),
        "tries": str(tries),
        "attempt_id": result.attempt_id,
        "prompt": prompt,
        "temperature": str(stage_cfg.temperature),
        "top_p": str(stage_cfg.top_p),
        "top_k": str(stage_cfg.top_k),
        "reasoning_budget": str(stage_cfg.reasoning_budget_tokens),
        "started_at": result.started_at,
        "ended_at": result.ended_at,
        "status": result.status,
        "moment_id": result.moment_id or "—",
        "hop_count": str(result.hop_count),
        "stop_reason": result.stop_reason or "—",
        "spoke": str(result.spoke),
        "tools": ", ".join(result.tools) if result.tools else "(none)",
        "per_hop_finish_reason": (
            ", ".join(result.per_hop_finish_reason)
            if result.per_hop_finish_reason
            else "—"
        ),
        "reasoning_len": str(result.reasoning_len),
        "markers_content": str(result.markers_content),
        "markers_reasoning": str(result.markers_reasoning),
        "markers_strip_c": str(result.markers_strip_c),
        "markers_strip_r": str(result.markers_strip_r),
        "flood": "Y" if result.flood else "N",
        "glass_speak": "Y" if result.glass_speak else "N",
        "free_text_only": "Y" if result.free_text_only else "N",
        "latency_s": str(result.latency_s),
        "feel": str(result.feel),
        "tape_path": result.tape_path,
        "messages_path": result.messages_path,
        "export_dir": result.export_dir,
        "elyra_home": result.elyra_home,
        "dim_flood": result.dim_flood,
        "dim_tools": result.dim_tools,
        "dim_speak": result.dim_speak,
        "dim_feel": result.dim_feel,
        "dim_freetext": result.dim_freetext,
        "dim_flood_notes": result.dim_flood_notes,
        "dim_tools_notes": result.dim_tools_notes,
        "dim_speak_notes": result.dim_speak_notes,
        "dim_feel_notes": result.dim_feel_notes,
        "dim_freetext_notes": result.dim_freetext_notes,
        "notes": result.notes or "—",
        "content_snippet": result.content_snippet or "(empty)",
        "reasoning_snippet": result.reasoning_snippet or "(empty)",
        "glass_snippet": result.glass_snippet or "(none)",
    }

    def repl(m: re.Match[str]) -> str:
        key = m.group(1).strip()
        return mapping.get(key, m.group(0))

    return re.sub(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", repl, tpl)


def write_scorecard(
    result: AttemptResult,
    *,
    stage: int,
    tries: int,
    stage_cfg: StageConfig,
    prompt: str,
) -> Path:
    text = render_scorecard(
        result, stage=stage, tries=tries, stage_cfg=stage_cfg, prompt=prompt
    )
    out = _HERE / "logs" / f"scorecard-{result.attempt_id}.md"
    out.write_text(text, encoding="utf-8")
    _LOG.info("wrote %s", out)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Elyra live qualitative stage runner")
    p.add_argument("--stage", type=int, default=0)
    p.add_argument("--scenario", action="append", dest="scenarios", default=None)
    p.add_argument("--all-scenarios", action="store_true")
    p.add_argument("--try", type=int, dest="try_n", default=None)
    p.add_argument("--tries", type=int, default=DEFAULT_TRIES)
    p.add_argument("--llama-host", default="127.0.0.1")
    p.add_argument("--llama-port", type=int, default=8080)
    p.add_argument(
        "--start-llama",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Start llama-server if not healthy (default: true)",
    )
    p.add_argument(
        "--keep-llama",
        action="store_true",
        help="Do not stop an owned llama-server on exit",
    )
    p.add_argument("--score-only", action="store_true")
    p.add_argument("--export-dir", type=Path, default=None)
    p.add_argument(
        "--scenarios-file",
        type=Path,
        default=None,
        help="Override scenarios.yaml path",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    stage_cfg = load_scenarios(args.scenarios_file)
    if args.stage != stage_cfg.stage:
        _LOG.warning(
            "CLI --stage %s differs from scenarios.yaml stage %s — using CLI",
            args.stage,
            stage_cfg.stage,
        )

    if args.score_only:
        if not args.export_dir:
            raise SystemExit("--score-only requires --export-dir")
        export_dir = args.export_dir.resolve()
        # parse attempt_id from dirname
        name = export_dir.name
        # stage-0_S-social_try-1
        m = re.match(r"stage-(\d+)_(.+)_try-(\d+)", name)
        if not m:
            raise SystemExit(f"cannot parse attempt id from {name}")
        stage, sid, try_n = int(m.group(1)), m.group(2), int(m.group(3))
        scen = next((s for s in stage_cfg.scenarios if s.id == sid), None)
        result = score_from_export(
            attempt_id=name,
            scenario_id=sid,
            try_n=try_n,
            export_dir=export_dir,
            status="ok",
            expects_tools=scen.expects_tools if scen else True,
            expects_speak=scen.expects_speak if scen else True,
        )
        result.elyra_home = str(export_dir / "home")
        write_scorecard(
            result,
            stage=stage,
            tries=args.tries,
            stage_cfg=stage_cfg,
            prompt=scen.prompt if scen else "",
        )
        print(json.dumps({"attempt_id": result.attempt_id, "flood": result.flood,
                          "tools": result.tools, "glass_speak": result.glass_speak},
                         indent=2))
        return 0

    # Select scenarios
    if args.all_scenarios or not args.scenarios:
        selected = list(stage_cfg.scenarios)
    else:
        by_id = {s.id: s for s in stage_cfg.scenarios}
        selected = []
        for sid in args.scenarios:
            if sid not in by_id:
                raise SystemExit(
                    f"unknown scenario {sid!r}; have {list(by_id)}"
                )
            selected.append(by_id[sid])

    try_list = (
        [args.try_n]
        if args.try_n is not None
        else list(range(1, args.tries + 1))
    )

    llama = ensure_llama(
        host=args.llama_host,
        port=args.llama_port,
        start_if_needed=args.start_llama,
    )

    results: list[AttemptResult] = []
    try:
        for scen in selected:
            for try_n in try_list:
                _LOG.info("=== %s try %s/%s ===", scen.id, try_n, args.tries)
                result = run_attempt(
                    scen,
                    stage=args.stage,
                    try_n=try_n,
                    tries=args.tries,
                    stage_cfg=stage_cfg,
                    llama=llama,
                )
                write_scorecard(
                    result,
                    stage=args.stage,
                    tries=args.tries,
                    stage_cfg=stage_cfg,
                    prompt=scen.prompt,
                )
                results.append(result)
                print(
                    f"{result.attempt_id}: status={result.status} "
                    f"hops={result.hop_count} stop={result.stop_reason} "
                    f"tools={result.tools} flood={result.flood} "
                    f"speak={result.glass_speak} feel={result.feel} "
                    f"latency={result.latency_s}s"
                )
    finally:
        if llama.owned and not args.keep_llama:
            _LOG.info("stopping owned llama-server")
            llama.stop()

    # Summary table to stdout
    print("\n## Summary")
    print(
        "| attempt | status | hops | stop | tools | flood | speak | free_text | feel | s |"
    )
    print(
        "|---------|--------|------|------|-------|-------|-------|-----------|------|---|"
    )
    for r in results:
        print(
            f"| {r.attempt_id} | {r.status} | {r.hop_count} | {r.stop_reason} | "
            f"{','.join(r.tools) or '—'} | {'Y' if r.flood else 'N'} | "
            f"{'Y' if r.glass_speak else 'N'} | {'Y' if r.free_text_only else 'N'} | "
            f"{r.feel} | {r.latency_s} |"
        )

    summary_path = _HERE / "logs" / "runs" / f"stage-{args.stage}-batch-summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(
            [
                {
                    "attempt_id": r.attempt_id,
                    "status": r.status,
                    "hop_count": r.hop_count,
                    "stop_reason": r.stop_reason,
                    "tools": r.tools,
                    "flood": r.flood,
                    "glass_speak": r.glass_speak,
                    "free_text_only": r.free_text_only,
                    "feel": r.feel,
                    "latency_s": r.latency_s,
                    "markers_content": r.markers_content,
                    "markers_reasoning": r.markers_reasoning,
                    "dims": {
                        "flood": r.dim_flood,
                        "tools": r.dim_tools,
                        "speak": r.dim_speak,
                        "feel": r.dim_feel,
                        "freetext": r.dim_freetext,
                    },
                }
                for r in results
            ],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _LOG.info("batch summary → %s", summary_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
