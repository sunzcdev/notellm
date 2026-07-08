from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from notellm.ir import Context

TOOL_SCHEMA = {
    "name": "create_notebook",
    "description": "Create a new notebook for organizing sources and generations.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Notebook name (required)"},
            "description": {"type": "string", "description": "Optional notebook description"},
        },
        "required": ["name"],
    },
}


def _validate_notebook_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("Notebook name is required")
    if len(name) > 200:
        name = name[:200]
    return name


async def run(args: dict, ctx: Context) -> dict:
    name = _validate_notebook_name(args.get("name", ""))
    desc = args.get("description", "")
    r = ctx.client.post("/api/notebooks", {"name": name, "description": desc})
    ctx.client.invalidate_cache("notebooks")
    return {"id": r["id"], "name": r.get("name", name)}
