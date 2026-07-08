from __future__ import annotations

import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from notellm.ir import Context

_CLEAN_FOLDER = re.compile(r'[^-_.a-zA-Z0-9一-鿿]+')

TOOL_SCHEMA = {
    "name": "save_to_obsidian",
    "description": "Save Notellm output to Obsidian vault as Markdown. Use when user asks '存到Obsidian'/'保存到本地'/'写入笔记'. File goes to /home/ubuntu/notebooks/<topic>/ for you to review later.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "Folder name, e.g. 'AI Agent'"},
            "content": {"type": "string", "description": "Full text to save"},
            "source": {"type": "string", "description": "Source label, e.g. 'Dense Summary'"},
        },
        "required": ["content"],
    },
}


async def run(args: dict, ctx: Context) -> dict:
    topic = args.get("topic", "")
    content = args.get("content", "")
    source = args.get("source", "")

    if not content.strip():
        return {"error": "Content is empty, nothing to save"}

    ts = time.strftime("%Y%m%d_%H%M")
    date_str = time.strftime("%Y-%m-%d %H:%M")
    topic_slug = _CLEAN_FOLDER.sub("_", topic.strip() or "Notellm")[:40] or "Notellm"
    src_slug = _CLEAN_FOLDER.sub("_", source.strip() or "summary")[:30] or "summary"

    vault_dir = Path(ctx.config.obsidian_vault) / "wiki" / "notellm" / topic_slug / src_slug
    vault_dir.mkdir(parents=True, exist_ok=True)

    md = f"""---
created: {date_str}
source: {source or "notellm"}
topic: {topic or ""}
type: notellm_output
---

# {topic or "Notellm 产出"}

> {date_str} | {source or "Notellm 采集"}

---

{content}

---

*由 Notellm 生成*
"""
    filename = f"{ts}.md"
    filepath = vault_dir / filename
    filepath.write_text(md, encoding="utf-8")

    return {
        "filepath": str(filepath),
        "filename": filename,
        "folder": str(vault_dir),
        "char_count": len(content),
    }
