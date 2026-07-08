from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from notellm.ir import Context

TOOL_SCHEMA = {
    "name": "list_modes",
    "description": "List available generation modes (transformations + podcast). Cached for 30s.",
    "inputSchema": {"type": "object", "properties": {}},
}


async def run(args: dict, ctx: Context) -> dict:
    def _fetch():
        r = ctx.client.get("/api/transformations")
        modes = []
        if isinstance(r, list):
            for t in r:
                modes.append({"name": t["name"], "description": t.get("description", "")})
        modes.append({"name": "podcast", "description": "Generate a podcast episode"})
        return modes

    modes = ctx.client.cached("modes", ctx.config.cache_ttl, _fetch)
    return {"modes": modes}
