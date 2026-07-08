from __future__ import annotations

import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from notellm.hooks import log

if TYPE_CHECKING:
    from notellm.ir import Context

TOOL_SCHEMA = {
    "name": "check_job",
    "description": "Check the status and result of a submitted generation job.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "Job ID returned by notellm_generate"},
        },
        "required": ["job_id"],
    },
}


async def run(args: dict, ctx: Context) -> dict:
    from notellm.client import OnbError

    job_id = args.get("job_id", "")
    r = ctx.client.get(f"/api/commands/jobs/{job_id}")
    if not isinstance(r, dict):
        return {"job_id": job_id, "error": "Unexpected response"}

    status = r.get("status", "unknown")
    result = r.get("result")
    error_message = r.get("error_message")

    out = {
        "job_id": job_id,
        "status": status,
        "result": result,
        "error_message": error_message,
        "progress": r.get("progress"),
        "created": r.get("created"),
        "updated": r.get("updated"),
    }

    if status == "completed" and isinstance(result, dict) and result.get("source_id"):
        _fetch_insight_content(out, result, job_id, ctx)

    if "output" in out and out["output"].get("content", "").strip():
        _auto_save_results(out, result, job_id, ctx)

    if status == "completed" and isinstance(result, dict) and result.get("episode_id"):
        ep_id = result["episode_id"]
        ep_short = ep_id.replace("podcast_episode:", "")
        audio_url = f"/api/podcasts/episodes/{ep_short}/audio"
        out["output"] = {
            "type": "podcast_episode",
            "episode_id": ep_id,
            "audio_path": result.get("audio_file_path", ""),
            "audio_url": audio_url,
            "full_audio_url": f"{ctx.config.api_url}{audio_url}",
        }

    return out


def _fetch_insight_content(out: dict, result: dict, job_id: str, ctx: Context) -> None:
    from notellm.client import OnbError

    sid = result["source_id"]
    if not sid.startswith("source:"):
        sid = "source:" + sid
    tx_type = result.get("transformation_id", "")

    for attempt in range(3):
        try:
            insights = ctx.client.get(f"/api/sources/{sid}/insights", timeout=10)
            if isinstance(insights, list) and insights:
                if tx_type:
                    matching = [i for i in insights if tx_type in (i.get("insight_type", "") or "")]
                    insight = matching[0] if matching else insights[-1]
                else:
                    insight = insights[-1]
                if insight and insight.get("content", "").strip():
                    out["output"] = {
                        "type": "insight",
                        "insight_type": insight.get("insight_type", ""),
                        "content": insight.get("content", ""),
                        "source_id": sid,
                    }
                    break
            if attempt < 2:
                time.sleep(2)
        except OnbError:
            break


def _auto_save_results(out: dict, result: dict, job_id: str, ctx: Context) -> None:
    from notellm.client import OnbError
    from .generate import _JOB_NOTEBOOK_MAP

    nb_id = ""
    if isinstance(result, dict) and result.get("source_id"):
        try:
            sid = result["source_id"]
            if not sid.startswith("source:"):
                sid = "source:" + sid
            src_info = ctx.client.get(f"/api/sources/{sid}", timeout=8)
            if isinstance(src_info, dict):
                nbs = src_info.get("notebooks", [])
                nb_id = nbs[0] if nbs else ""
        except Exception:
            nb_id = _JOB_NOTEBOOK_MAP.get(job_id, "")
    else:
        nb_id = _JOB_NOTEBOOK_MAP.get(job_id, "")

    if not nb_id:
        return

    insight_type = out["output"].get("insight_type", "总结")
    content = out["output"]["content"]
    note_title = f"{insight_type} - {time.strftime('%Y-%m-%d %H:%M')}"

    try:
        ctx.client.post("/api/notes", {
            "title": note_title,
            "content": content,
            "note_type": "ai",
            "notebook_id": nb_id,
        }, timeout=15)
        log("INFO", "Auto-saved to ONB Note", job_id=job_id, notebook=nb_id)
    except OnbError as e:
        log("WARN", "Auto-save to ONB Note failed", job_id=job_id, error=str(e))

    try:
        try:
            nb_info = ctx.client.get(f"/api/notebooks/{nb_id}", timeout=10)
            nb_name = nb_info.get("name", "unknown") if isinstance(nb_info, dict) else "unknown"
        except Exception:
            nb_name = "unknown"

        nb_slug = re.sub(r'[^-_.a-zA-Z0-9一-鿿]+', '_', nb_name)[:40] or "notebook"
        form_slug = re.sub(r'[^-_.a-zA-Z0-9一-鿿]+', '_', insight_type)[:30] or "summary"
        vault_dir = Path(ctx.config.obsidian_vault) / "wiki" / "notellm" / nb_slug / form_slug
        vault_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M")
        md = f"""---
created: {time.strftime('%Y-%m-%d %H:%M')}
source: notellm_summary
type: notellm_output
---

# {insight_type} - {nb_name}

{content}

---
*由 Notellm 自动生成*
"""
        fp = vault_dir / f"{ts}.md"
        fp.write_text(md, encoding="utf-8")
        out["obsidian_file"] = str(fp)
        log("INFO", "Auto-saved to Obsidian", job_id=job_id, path=str(fp))
    except Exception as e:
        log("WARN", "Auto-save to Obsidian failed", job_id=job_id, error=str(e))
