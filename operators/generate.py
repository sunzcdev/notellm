from __future__ import annotations

import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from notellm.hooks import log

if TYPE_CHECKING:
    from notellm.ir import Context

_JOB_NOTEBOOK_MAP: dict[str, str] = {}

TOOL_SCHEMA = {
    "name": "generate",
    "description": "Generate content from a notebook. Default: merge all sources into one and run a single transformation (fast, no job polling). Set per_source=true to run one job per source instead. Use mode='podcast' for audio.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "notebook_id": {"type": "string", "description": "Notebook ID"},
            "mode": {"type": "string", "description": "Generation mode — transformation name or 'podcast'"},
            "per_source": {"type": "boolean", "description": "If true: one job per source (legacy). Default: false (merge all sources into one)."},
        },
        "required": ["notebook_id", "mode"],
    },
}


async def run(args: dict, ctx: Context) -> dict:
    notebook_id = args.get("notebook_id", "")
    mode = args.get("mode", "")
    per_source = args.get("per_source", False)

    if mode.lower() == "podcast":
        return _generate_podcast(notebook_id, ctx)
    if per_source:
        return _generate_transform(notebook_id, mode, ctx)
    return _generate_merged(notebook_id, mode, ctx)


def _generate_podcast(notebook_id: str, ctx: Context) -> dict:
    nb = ctx.client.get(f"/api/notebooks/{notebook_id}")
    ep_name = nb.get("name", "Podcast") if isinstance(nb, dict) else "Podcast"
    ep_profile, sp_profile = ctx.config.podcast_profiles
    r = ctx.client.post("/api/podcasts/generate", {
        "notebook_id": notebook_id,
        "episode_name": f"{ep_name} - Notellm",
        "episode_profile": ep_profile,
        "speaker_profile": sp_profile,
    })
    out = {"job_id": r["job_id"], "mode": "podcast", "status": "queued"}
    if "note" in r:
        out["note"] = "Generation runs async; check with notellm_check_job"
    return out


def _generate_merged(notebook_id: str, mode: str, ctx: Context) -> dict:
    tx = ctx.client.get("/api/transformations")
    if not isinstance(tx, list):
        return {"error": "Cannot fetch transformations"}

    tid, tname = _find_transformation(tx, mode)
    if not tid:
        return {"error": f"Transformation '{mode}' not found"}

    srcs, merged_text = _build_merged_text(notebook_id, ctx)
    if not srcs:
        return {"error": "Notebook has no sources"}
    if len(merged_text) < 20:
        return {"error": "Merged content too short"}

    from notellm.client import OnbError
    try:
        r = ctx.client.post("/api/transformations/execute", {
            "transformation_id": tid,
            "input_text": merged_text,
        }, timeout=300)

        if isinstance(r, dict) and "output" in r:
            output = r["output"]
            _auto_save_note(notebook_id, tname, output, ctx)
            obs_path = _auto_save_obsidian(notebook_id, tname, output, ctx)
            return {
                "mode": tname,
                "merged": True,
                "source_count": len(srcs),
                "total_chars": len(merged_text),
                "output": output,
                "obsidian_file": obs_path,
                "note": "Merged generation complete — no job polling needed",
            }
        return {"error": f"Transformation returned: {r}"}
    except OnbError as e:
        return {"error": f"Transformation failed: {e}"}


def _generate_transform(notebook_id: str, mode: str, ctx: Context) -> dict:
    tx = ctx.client.get("/api/transformations")
    if not isinstance(tx, list):
        return {"error": "Cannot fetch transformations"}

    tid, tname = _find_transformation(tx, mode)
    if not tid:
        return {"error": f"Transformation '{mode}' not found. Available: {[t['name'] for t in tx]}"}

    ctx_resp = ctx.client.post(f"/api/notebooks/{notebook_id}/context",
                               {"notebook_id": notebook_id})
    srcs = ctx_resp.get("sources", []) if isinstance(ctx_resp, dict) else []
    if not srcs:
        return {"error": "Notebook has no sources to transform"}

    from notellm.client import OnbError
    jobs = []
    for s in srcs:
        sid = s.get("id")
        if not sid:
            continue
        try:
            jr = ctx.client.post("/api/commands/jobs", {
                "command": "run_transformation",
                "app": "open_notebook",
                "input": {"source_id": sid, "transformation_id": tid},
            })
            jid = jr["job_id"]
            _JOB_NOTEBOOK_MAP[jid] = notebook_id
            jobs.append({
                "job_id": jid,
                "source_title": s.get("title", ""),
                "source_id": sid,
            })
        except OnbError as e:
            log("WARN", f"Failed to submit job for source {sid}", error=str(e))

    if not jobs:
        return {"error": "Failed to submit any transformation jobs"}

    return {
        "mode": tname,
        "jobs": jobs,
        "summary": f"Submitted {len(jobs)} job(s) for '{tname}'",
        "note": "Check individual jobs with notellm_check_job(job_id=...)",
    }


def _find_transformation(tx: list, mode: str) -> tuple:
    for t in tx:
        if t["name"].lower() == mode.lower():
            return t["id"], t["name"]
    for t in tx:
        if mode.lower() in t["name"].lower():
            return t["id"], t["name"]
    return None, None


def _build_merged_text(notebook_id: str, ctx: Context) -> tuple[list, str]:
    ctx_resp = ctx.client.post(f"/api/notebooks/{notebook_id}/context",
                               {"notebook_id": notebook_id})
    srcs = ctx_resp.get("sources", []) if isinstance(ctx_resp, dict) else []
    if not srcs:
        return [], ""
    parts = []
    for s in srcs:
        sid = s.get("id", "")
        title = s.get("title", "Untitled")
        content = ""
        if sid:
            try:
                sd = ctx.client.get(f"/api/sources/{sid}", timeout=10)
                if isinstance(sd, dict):
                    content = sd.get("full_text", "") or ""
            except Exception:
                pass
        if not content:
            content = s.get("content", "") or ""
        if content:
            parts.append(f"## {title}\n\n{content.strip()}")
    return srcs, "\n\n---\n\n".join(parts)


def _auto_save_note(notebook_id: str, tname: str, output: str, ctx: Context) -> None:
    try:
        ctx.client.post("/api/notes", {
            "title": f"{tname} - {time.strftime('%Y-%m-%d %H:%M')}",
            "content": output, "note_type": "ai",
            "notebook_id": notebook_id,
        }, timeout=15)
    except Exception:
        pass


def _auto_save_obsidian(notebook_id: str, tname: str, output: str, ctx: Context) -> str:
    try:
        try:
            nb_info = ctx.client.get(f"/api/notebooks/{notebook_id}", timeout=8)
            nb_name = nb_info.get("name", "unknown") if isinstance(nb_info, dict) else "unknown"
        except Exception:
            nb_name = "unknown"

        nb_slug = re.sub(r'[^-_.a-zA-Z0-9一-鿿]+', '_', nb_name)[:40] or "notebook"
        form_slug = re.sub(r'[^-_.a-zA-Z0-9一-鿿]+', '_', tname)[:30] or "summary"
        vault_dir = Path(ctx.config.obsidian_vault) / "wiki" / "notellm" / nb_slug / form_slug
        vault_dir.mkdir(parents=True, exist_ok=True)
        fp = vault_dir / f"{time.strftime('%Y%m%d_%H%M')}.md"
        fp.write_text(f"""---
created: {time.strftime('%Y-%m-%d %H:%M')}
source: notellm_{tname}
type: notellm_output
---

# {tname} - {nb_name}

{output}

---
*由 Notellm 自动生成*
""", encoding="utf-8")
        return str(fp)
    except Exception:
        return ""
