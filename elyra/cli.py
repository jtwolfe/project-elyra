"""Elyra operator CLI — single-command application start.

Config merge: defaults < elyra.toml < data/runtime/provider.json < explicit CLI.
Hermetic UI path is ``--stub-llm`` only. ``provider=local`` fails closed
(no local inference process this pass).
"""

from __future__ import annotations

import argparse
import logging
import sys

from elyra.config import resolve_paths
from elyra.llm.auth import VALID_SOURCES
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


if __name__ == "__main__":
    raise SystemExit(main())
