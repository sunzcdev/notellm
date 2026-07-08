from __future__ import annotations

from typing import TYPE_CHECKING

from notellm.client import OnbError

if TYPE_CHECKING:
    from notellm.ir import Context

TOOL_SCHEMA = {
    "name": "check_jobs",
    "description": "Check status of multiple jobs in one call.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "job_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Array of job IDs",
            },
        },
        "required": ["job_ids"],
    },
}


async def run(args: dict, ctx: Context) -> dict:
    from . import check_job

    job_ids = args.get("job_ids", [])
    results = []
    for jid in job_ids:
        try:
            r = await check_job.run({"job_id": jid}, ctx)
            results.append(r)
        except OnbError as e:
            results.append({"job_id": jid, "error": str(e)})
    return {"jobs": results}
