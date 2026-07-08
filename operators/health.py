from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from notellm.ir import Context

__version__ = "0.6.0"

_AR_DIR = os.path.expanduser("~/projects/agent-reach")
_AR_VENV = os.path.join(_AR_DIR, ".venv", "bin", "python3")
_PIPELINE_SCRIPT = os.path.expanduser("~/.hermes/scripts/douyin-pipeline.py")
_BETTER_DOUYIN = os.path.expanduser("~/projects/better-douyin")

TOOL_SCHEMA = {
    "name": "health",
    "description": "Check system health: ONB API reachability, disk space, and available tools.",
    "inputSchema": {"type": "object", "properties": {}},
}


async def run(args: dict, ctx: Context) -> dict:
    issues = []
    info = {
        "version": __version__,
        "onb_api": ctx.config.api_url,
        "auth_configured": bool(ctx.config.password),
    }

    try:
        h = ctx.client.get("/health", timeout=10)
        info["onb_healthy"] = True
        if isinstance(h, dict):
            info["onb_status"] = h
    except Exception as e:
        from notellm.client import OnbAuthError
        if isinstance(e, OnbAuthError):
            info["onb_healthy"] = True
            info["onb_auth"] = "required"
        else:
            info["onb_healthy"] = False
            info["onb_error"] = str(e)
            issues.append(f"ONB API unreachable: {e}")

    info["agent_reach_available"] = os.path.isfile(_AR_VENV)
    if not info["agent_reach_available"]:
        issues.append("agent-reach not found (some collect modes unavailable)")

    info["better_douyin_available"] = os.path.isdir(_BETTER_DOUYIN)
    info["douyin_pipeline_available"] = os.path.isfile(_PIPELINE_SCRIPT)

    try:
        cdir = ctx.config.collection_dir if os.path.isdir(ctx.config.collection_dir) else "/tmp"
        st = os.statvfs(cdir)
        free_mb = (st.f_frsize * st.f_bavail) / (1024 * 1024)
        info["disk_free_mb"] = round(free_mb, 1)
        if free_mb < 100:
            issues.append(f"Low disk space: {free_mb:.0f} MB free")
    except Exception:
        pass

    return {"status": "healthy" if not issues else "degraded", "issues": issues, "info": info}
