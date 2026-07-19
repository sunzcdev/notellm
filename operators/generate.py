from __future__ import annotations
from operators.model_resolver import ModelConfigResolver

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
            "model": {"type": "string", "description": "可选：指定用于生成脚本的 LLM 模型。"},
        },
        "required": ["notebook_id", "mode"],
    },
}


async def run(args: dict, ctx: Context) -> dict:
    notebook_id = args.get("notebook_id", "")
    mode = args.get("mode", "")
    per_source = args.get("per_source", False)
    model = args.get("model")

    if mode.lower() == "podcast":
        return await _generate_podcast(notebook_id, ctx, model=model)
    if mode.lower() == "trio":
        return await _generate_trio_podcast(notebook_id, ctx, model=model)
    if mode.lower() == "summary_podcast":
        return await _generate_summary_podcast(notebook_id, ctx, model=model)
    if per_source:
        return _generate_transform(notebook_id, mode, ctx)
    return _generate_merged(notebook_id, mode, ctx)


def _build_podcast_filename(nb: dict, ctx: Context) -> str:
    """Extract core argument from notebook content via analysis and build a safe filename."""
    import json
    content = nb.get("content", "") if isinstance(nb, dict) else str(nb)
    analysis_str = ctx.client.post("/api/transformations/execute", {
        "transformation_id": "transformation:6ht5kngsxqjx3y8g4yoq",
        "input_text": f"""请分析以下内容，提取出用于支撑播客对话的元数据。
请以 JSON 格式输出，包含以下字段：
1. "logic_structure": {{ "core_argument": "核心论点是什么？" }}
内容: {content[:10000]}""",
    })
    try:
        analysis = json.loads(analysis_str.get("output", "{}"))
        core = analysis.get("logic_structure", {}).get("core_argument", "podcast")
        safe_name = "".join([c if c.isalnum() else "_" for c in core[:20]])
    except Exception:
        safe_name = "podcast"

    import os
    out_dir = ctx.config.podcast_output_dir
    os.makedirs(out_dir, exist_ok=True)
    return os.path.join(out_dir, f"{safe_name}.wav")


async def _generate_podcast(notebook_id: str, ctx: Context, model: str | None = None) -> dict:
    nb = ctx.client.get(f"/api/notebooks/{notebook_id}")
    ep_name = nb.get("name", "Podcast") if isinstance(nb, dict) else "Podcast"

    filename = _build_podcast_filename(nb, ctx)

    ep_profile, sp_profile = ctx.config.podcast_profiles

    payload = {
        "notebook_id": notebook_id,
        "episode_name": f"{ep_name} - Notellm",
        "episode_profile": ep_profile,
        "speaker_profile": sp_profile,
        "expected_filename": filename,
    }

    if model:
        payload["model"] = ModelConfigResolver.resolve_model(model, ctx)

    r = ctx.client.post("/api/podcasts/generate", payload)
    if not isinstance(r, dict) or "job_id" not in r:
        return {"error": f"Unexpected response from podcast generation: {r}"}
    out = {"job_id": r["job_id"], "mode": "podcast", "status": "queued",
           "expected_filename": filename}
    if "note" in r:
        out["note"] = "Generation runs async; check with notellm_check_job"
    return out


async def _generate_trio_podcast(notebook_id: str, ctx: Context, model: str | None = None) -> dict:
    # 1. Generate custom script
    script = await _generate_trio_script(notebook_id, ctx)

    # 2. Call existing generate podcast logic with custom script
    nb = ctx.client.get(f"/api/notebooks/{notebook_id}")
    ep_name = nb.get("name", "Podcast") if isinstance(nb, dict) else "Podcast"

    filename = _build_podcast_filename(nb, ctx)

    ep_profile, sp_profile = ctx.config.podcast_profiles

    payload = {
        "notebook_id": notebook_id,
        "episode_name": f"{ep_name} - Notellm (Trio)",
        "episode_profile": ep_profile,
        "speaker_profile": sp_profile,
        "expected_filename": filename,
        "custom_script": script,
    }

    if model:
        payload["model"] = ModelConfigResolver.resolve_model(model, ctx)

    r = ctx.client.post("/api/podcasts/generate", payload)
    if not isinstance(r, dict) or "job_id" not in r:
        return {"error": f"Unexpected response from podcast generation: {r}"}

    out = {"job_id": r["job_id"], "mode": "trio_podcast", "status": "queued",
           "expected_filename": filename}
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


async def _generate_trio_script(notebook_id: str, ctx: Context) -> str:
    # 1. Get content
    nb = ctx.client.get(f"/api/notebooks/{notebook_id}")
    content = nb.get("content", "") if isinstance(nb, dict) else str(nb)

    # 2. Architect Agent: Create Outline
    arch_prompt = f"""
    Create a 4-step 'Trio Discussion' outline for a podcast based on this content.
    Steps: Anchor, Deconstruct, Collision, Takeaway.
    Include 2 nodes where the user 'Zhenzhao' (a silent listener/friend) is mentioned
    or addressed by Maia and Kai.
    Content: {content[:5000]}
    """
    outline = ctx.client.post("/api/transformations/execute", {
        "transformation_id": "transformation:6ht5kngsxqjx3y8g4yoq",
        "input_text": arch_prompt,
    })

    # 3. Dramatist Agent: Create Script
    dram_prompt = f"""
    Convert the following outline into a natural, spoken-word 3-person podcast script.
    Host 1: Maia (Intellectual, Gentle). Host 2: Kai (Expert, Hardcore).
    User: Zhenzhao (Silent listener, friend, present).
    Use behavioral cues. Maia and Kai must talk to each other but also occasionally
    mention Zhenzhao naturally.
    Outline: {outline.get('output', '')}
    """
    script_res = ctx.client.post("/api/transformations/execute", {
        "transformation_id": "dramatist_mode",
        "input_text": dram_prompt,
    })
    return script_res.get("output", "")

async def _generate_summary_podcast(notebook_id: str, ctx: Context, model: str | None = None) -> dict:
    # 1. Get summary
    # We'll use "summary" transformation
    tx = ctx.client.get("/api/transformations")
    tid, tname = _find_transformation(tx, "summary")
    if not tid:
        return {"error": "Summary transformation not found"}

    srcs, merged_text = _build_merged_text(notebook_id, ctx)
    r = ctx.client.post("/api/transformations/execute", {
        "transformation_id": tid,
        "input_text": merged_text,
    }, timeout=300)
    summary = r.get("output", "")

    # 2. Convert Summary to Trio Script
    script_prompt = f"""
    Convert the following summary into a natural, spoken-word 3-person podcast script.
    Host 1: Maia (Intellectual, Gentle). Host 2: Kai (Expert, Hardcore).
    User: Zhenzhao (Silent listener, friend, present).
    Format as:
    **Maia**: ...
    **Kai**: ...

    Use behavioral cues. Maia and Kai must talk to each other but also occasionally
    mention Zhenzhao naturally.
    Summary: {summary}
    """

    payload = {
        "transformation_id": "dramatist_mode",
        "input_text": script_prompt,
    }

    if model:
        # 针对 transformation 的 model 覆盖，需要按需扩展 resolver
        payload["model"] = ModelConfigResolver.resolve_model(model, ctx)

    script_res = ctx.client.post("/api/transformations/execute", payload)
    script = script_res.get("output", "")

    # 3. Call TTS synthesize
    from operators.synthesize import run as tts_run
    res = await tts_run({"text": script, "voice": "Maia", "model": "fb8fvu9rfdj9ectik8dw"}, ctx)

    return res
