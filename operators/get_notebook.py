from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from notellm.ir import Context

TOOL_SCHEMA = {
    "name": "get_notebook",
    "description": "Get notebook details including source list. NEVER curl http://127.0.0.1:5055 directly — always use this tool instead.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "notebook_id": {"type": "string", "description": "Notebook ID"},
        },
        "required": ["notebook_id"],
    },
}


async def run(args: dict, ctx: Context) -> dict:
    notebook_id = args.get("notebook_id", "")
    nb = ctx.client.get(f"/api/notebooks/{notebook_id}")
    if not isinstance(nb, dict) or "error" in nb:
        return {"error": f"Notebook not found: {notebook_id}"}

    ctx_resp = ctx.client.post(f"/api/notebooks/{notebook_id}/context",
                               {"notebook_id": notebook_id})
    sources = []
    if isinstance(ctx_resp, dict):
        for s in ctx_resp.get("sources", []):
            sources.append({
                "id": s.get("id", ""),
                "title": s.get("title", s.get("content", ""))[:80],
            })

    return {
        "id": nb.get("id", ""),
        "name": nb.get("name", ""),
        "description": nb.get("description", ""),
        "source_count": nb.get("source_count", 0),
        "note_count": nb.get("note_count", 0),
        "sources": sources,
    }
