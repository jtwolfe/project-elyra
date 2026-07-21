"""Elyra operator CLI — single-command application start."""

from __future__ import annotations

import argparse
import sys

from elyra.config import resolve_paths
from elyra.llm.constants import CONTEXT_WINDOW_TOKENS
from elyra.runtime.config import RuntimeConfig
from elyra.runtime.supervisor import run_supervisor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="elyra", description="Elyra operator CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser(
        "start",
        help="Start llama-server, API, Web UI, and presence worker",
    )
    start.add_argument(
        "--no-llama",
        action="store_true",
        help="Skip llama-server (stub LLM + API/UI only)",
    )
    start.add_argument(
        "--stub-llm",
        action="store_true",
        help="Use stub chat client even if llama is up",
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "start":
        return 1

    paths = resolve_paths()
    config = RuntimeConfig(
        api_host=args.api_host,
        api_port=args.api_port,
        start_llama_server=not args.no_llama,
        context_tokens=args.context_tokens,
    )
    print(f"Elyra home: {paths.home}")
    print(f"Web UI:     http://{config.api_host}:{config.api_port}/")
    print(f"llama:      {'off' if args.no_llama else 'on'}")
    if args.context_tokens:
        print(f"context -c: {args.context_tokens}")
    run_supervisor(
        paths=paths,
        config=config,
        use_stub_llm=args.stub_llm or args.no_llama,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
