from __future__ import annotations
import re
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from notellm.ir import Context


async def generate_analysis(content: str, ctx: Context) -> str:
    prompt = f"""
    请分析以下内容，提取出用于支撑播客对话的元数据。
    请以 JSON 格式输出，包含以下字段：

    1. "logic_structure": {{
        "core_argument": "核心论点是什么？",
        "counter_points": ["对立观点/常见误区有哪些？"],
        "logical_gaps": ["逻辑链条中哪些地方需要补充假设或例子？"]
    }},
    2. "tone_profile": {{
        "sentiment": "客观/激情/批判/幽默",
        "intellectual_depth": "入门普及/深度解析/硬核探究"
    }},
    3. "discussion_strategy": {{
        "maia_focus": "Maia（温柔思考者）适合从哪个角度切入？",
        "kai_focus": "Kai（硬核专家）适合挑战什么观点？",
        "zhenzhao_trigger": "在内容的什么位置适合 Zhenzhao（听众视角）插话提问？"
    }}

    内容: {content[:10000]}
    """
    res = await ctx.client.post("/api/transformations/execute", {
        "transformation_id": "transformation:6ht5kngsxqjx3y8g4yoq",
        "input_text": prompt,
    })
    return res.get("output", "{}")

async def generate_trio_script(notebook_id: str, ctx: Context) -> str:
    nb = ctx.client.get(f"/api/notebooks/{notebook_id}")
    content = nb.get("content", "") if isinstance(nb, dict) else str(nb)
    analysis = await generate_analysis(content, ctx)

    arch_prompt = f"""
    Create a 4-step 'Trio Discussion' outline based on this content.
    Steps: Anchor, Deconstruct, Collision, Takeaway.
    Mention user 'Zhenzhao' (silent listener) 2+ times.
    Analysis context: {analysis}
    Content: {content[:5000]}
    """
    outline = await ctx.client.post("/api/transformations/execute", {
        "transformation_id": "transformation:6ht5kngsxqjx3y8g4yoq",
        "input_text": arch_prompt,
    })

    dram_prompt = f"""
    Convert outline to natural 3-person script.
    Format: **Maia**: ... \n **Kai**: ...
    User: Zhenzhao (silent listener).
    Outline: {outline.get('output', '')}
    Context/Strategy: {analysis}
    """
    script_res = await ctx.client.post("/api/transformations/execute", {
        "transformation_id": "dramatist_mode",
        "input_text": dram_prompt,
    })

    script = script_res.get('output', '')
    if not re.search(r'\*\*(Maia|Kai)\*\*:', script):
        raise ValueError("Generated script format invalid: missing speaker tags.")

    # Extract topic from notebook content for Obsidian export
    topic = nb.get("title", nb.get("name", "Podcast Episode"))

    # Export to Obsidian automatically
    from .save_to_obsidian import run as save_to_obsidian
    await save_to_obsidian(
        {"topic": topic, "content": script, "source": "Trio Script Generation"},
        ctx
    )

    return script

async def generate_summary_podcast(notebook_id: str, ctx: Context) -> dict:
    tx = ctx.client.get("/api/transformations")
    tid, _ = _find_transformation(tx, "summary")
    if not tid: return {"error": "Summary transformation not found"}

    from .generate import _build_merged_text
    srcs, merged_text = _build_merged_text(notebook_id, ctx)
    r = ctx.client.post("/api/transformations/execute", {"transformation_id": tid, "input_text": merged_text}, timeout=300)
    summary = r.get("output", "")

    script_prompt = f"Convert to natural 3-person script (**Maia**/**Kai**/**Zhenzhao**). Summary: {summary}"
    script_res = await ctx.client.post("/api/transformations/execute", {
        "transformation_id": "dramatist_mode", "input_text": script_prompt,
    })
    script = script_res.get("output", "")

    from operators.synthesize import run as tts_run
    return await tts_run({"text": script, "voice": "Maia", "model": "fb8fvu9rfdj9ectik8dw"}, ctx)

def _find_transformation(tx: list, mode: str) -> tuple:
    for t in tx:
        if t["name"].lower() == mode.lower(): return t["id"], t["name"]
    return None, None
