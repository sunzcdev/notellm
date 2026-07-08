from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from notellm.ir import Context

TOOL_SCHEMA = {
    "name": "list_notebooks",
    "description": "List all notebooks with source counts. Results cached for 30s.",
    "inputSchema": {"type": "object", "properties": {}},
}


async def run(args: dict, ctx: Context) -> dict:
    r = ctx.client.cached("notebooks", ctx.config.cache_ttl,
                          lambda: ctx.client.get("/api/notebooks"))
    if not isinstance(r, list):
        return {"notebooks": []}
    return {
        "notebooks": [
            {"id": nb["id"], "name": nb["name"],
             "source_count": nb.get("source_count", 0)}
            for nb in r
        ]
    }
