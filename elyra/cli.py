"""Elyra operator CLI — single-command application start.

Config merge: defaults < elyra.toml < data/runtime/provider.json < explicit CLI.
``--no-llama`` only skips llama-server; it does **not** force StubChatClient
(use ``--stub-llm`` for that).
"""

from __future__ import annotations

import argparse
import logging
import sys

from elyra.config import resolve_paths
from elyra.llm.constants import CONTEXT_WINDOW_TOKENS
from elyra.runtime.config import load_merged_settings, runtime_config_from_settings
from elyra.runtime.provider_runtime import (
    credential_detail_message,
    format_usage_posture,
)
from elyra.runtime.supervisor import ElyraSupervisor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="elyra", description="Elyra operator CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser(
        "start",
        help="Start API, Web UI, presence worker (and llama-server when provider=local)",
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
        choices=("grok_build", "api_key"),
        default=None,
        help="Override active credential source",
    )
    start.add_argument(
        "--no-usage-meter",
        action="store_true",
        help="Disable hierarchical usage meter (debug)",
    )
    start.add_argument(
        "--no-llama",
        action="store_true",
        help=(
            "Skip llama-server only (does not force stub LLM). "
            "When provider=xai, llama is already not started."
        ),
    )
    start.add_argument(
        "--stub-llm",
        action="store_true",
        help="Use StubChatClient (only flag that forces stub)",
    )
    start.add_argument("--api-host", default="127.0.0.1")
    start.add_argument("--api-port", type=int, default=8787)
    start.add_argument(
        "--context-tokens",
        type=int,
        default=None,
        help=f"llama-server -c (default {CONTEXT_WINDOW_TOKENS}; lower if VRAM crashes)",
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
    if state.provider_name == "local":
        llama = "on" if config.start_llama_server else "off"
        print(f"llama:       {llama}")
    if config.context_tokens:
        print(f"context -c:  {config.context_tokens}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "start":
        return 1

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
        context_tokens=args.context_tokens,
    )

    # --no-llama does NOT force stub (Phase 0 footgun fix).
    use_stub = bool(args.stub_llm)
    config = runtime_config_from_settings(
        settings,
        no_llama=bool(args.no_llama),
        stub_llm=use_stub,
    )

    if args.no_llama and config.provider_name == "xai":
        print(
            "note: --no-llama ignored (provider=xai does not start llama)",
            file=sys.stderr,
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


if __name__ == "__main__":
    raise SystemExit(main())
