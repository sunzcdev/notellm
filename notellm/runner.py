from __future__ import annotations

import asyncio
import os
import sys
import traceback


def main() -> None:
    try:
        _run()
    except Exception:
        msg = traceback.format_exc()
        sys.stderr.write(f"[notellm] FATAL startup error:\n{msg}\n")
        sys.stderr.flush()
        sys.exit(1)


def _run() -> None:
    from .client import OnbClient
    from .hooks import StderrLogger, log
    from .ir import Context, OnbConfig
    from .mcp import serve
    from .registry import OperatorRegistry

    config = OnbConfig.from_env()
    client = OnbClient(config)
    ctx = Context(client=client, config=config)

    # Resolve operator_dir absolutely so it works from any CWD
    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    operator_dir = os.path.join(pkg_root, "operators")

    registry = OperatorRegistry()
    registry.discover(operator_dir)

    log("INFO", "notellm starting",
        version="0.6.0",
        operator_dir=operator_dir,
        operators_found=len(registry.names),
        api_url=config.api_url)

    hooks = [StderrLogger()]

    asyncio.run(serve(registry, ctx, hooks))


if __name__ == "__main__":
    main()
