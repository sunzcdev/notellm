from __future__ import annotations

import asyncio
import os

from .client import OnbClient
from .hooks import StderrLogger
from .ir import Context, OnbConfig
from .mcp import serve
from .registry import OperatorRegistry


def main() -> None:
    config = OnbConfig.from_env()
    client = OnbClient(config)
    ctx = Context(client=client, config=config)

    registry = OperatorRegistry()
    operator_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "operators")
    registry.discover(operator_dir)

    hooks = [StderrLogger()]

    asyncio.run(serve(registry, ctx, hooks))


if __name__ == "__main__":
    main()
