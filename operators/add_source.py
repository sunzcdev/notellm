from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from notellm.ir import Context

TOOL_SCHEMA = {
    "name": "add_source",
    "description": "Import a Markdown file as a source into a notebook.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "notebook_id": {"type": "string", "description": "Notebook ID"},
            "file_path": {"type": "string", "description": "Path to Markdown file with YAML frontmatter"},
        },
        "required": ["notebook_id", "file_path"],
    },
}


def _validate_file_path(file_path: str, max_size: int) -> str:
    from notellm.client import OnbError
    p = Path(file_path).resolve()
    if not p.exists():
        raise OnbError(f"File not found: {file_path}")
    if not p.is_file():
        raise OnbError(f"Not a file: {file_path}")
    size = p.stat().st_size
    if size > max_size:
        raise OnbError(f"File too large: {size} bytes (max {max_size})")
    if size == 0:
        raise OnbError(f"Empty file: {file_path}")
    return str(p)


async def run(args: dict, ctx: Context) -> dict:
    notebook_id = args.get("notebook_id", "")
    file_path = args.get("file_path", "")

    fp = _validate_file_path(file_path, ctx.config.max_file_size)
    with open(fp, encoding="utf-8") as f:
        text = f.read()

    title, body = "", text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            frontmatter, body = parts[1].strip(), parts[2].strip()
            m = re.search(r'^title:\s*["\']?([^"\'\n]+)["\']?', frontmatter, re.M)
            if m:
                title = m.group(1).strip()
    if not title:
        m = re.search(r'^#\s+(.+)$', body, re.M)
        if m:
            title = m.group(1).strip()
    if not title:
        title = os.path.splitext(os.path.basename(file_path))[0]

    if len(body) < 10:
        return {"warning": "Very short content (<10 chars)", "source_id": "", "title": title}

    r = ctx.client.post("/api/sources/json", {"type": "text", "title": title, "content": body})
    sid = r.get("id", "")
    if not sid:
        return {"error": "No source ID returned from ONB"}

    lr = ctx.client.post(f"/api/notebooks/{notebook_id}/sources/{sid}")
    ctx.client.invalidate_cache("notebooks")

    result = {"source_id": sid, "title": title}
    if "error" in (lr or {}):
        result["warning"] = f"Source created but link to notebook failed: {lr.get('error', 'unknown')}"
    return result
