"""python -m intent_parser [--host H] [--port P]"""
from __future__ import annotations

import argparse
import ipaddress
import logging
import os
import sys

import uvicorn

from .app import create_app
from .settings import Settings


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="intent_parser")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8100)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    if not _is_loopback(args.host) and not settings.auth_enabled:
        print("error: refusing to listen on a non-loopback address without INTENT_API_KEYS", file=sys.stderr)
        return 2
    if (why := settings.problem()):
        print(f"error: {why}", file=sys.stderr)
        return 2
    if settings.llm_provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        print("error: ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 2
    uvicorn.run(create_app(settings=settings), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
