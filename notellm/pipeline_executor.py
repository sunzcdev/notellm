from __future__ import annotations
from typing import Any, Dict
from .ir import Context
from .registry import OperatorRegistry
from operators.generate import run as generate_run
from operators.add_source import run as add_source_run

class PipelineConfig:
    def __init__(self):
        # 默认配置
        self.settings = {
            "collect": {"timeout": 300},
            "summarize": {"model": "claude-3-5", "timeout": 300},
            "podcast": {"model": "gpt-4", "timeout": 1200}
        }

    def update(self, pipeline: str, params: Dict[str, Any]):
        if pipeline in self.settings:
            self.settings[pipeline].update(params)
        else:
            raise KeyError(f"Unknown pipeline: {pipeline}")

class PipelineExecutor:
    def __init__(self, registry: OperatorRegistry):
        self.registry = registry
        self.config = PipelineConfig()

    async def collect_data(self, topic: str, source_input: str, source_type: str, ctx: Context) -> dict:
        # F 流程: 采集 (调用底层原子 Operator)
        # 1. 假设采集逻辑在 agent-reach/better-douyin 等，需确保已入库
        # 2. 调用 add_source_run 完成入库
        # 这里演示调用原子的 add_source_run 封装
        return await add_source_run({"notebook_id": topic, "file_path": source_input}, ctx)

    async def summarize_notebook(self, notebook_id: str, format: str, ctx: Context) -> dict:
        # G 流程: 总结
        # 1. 封装 generate_run 的 mode 调用
        return await generate_run({"notebook_id": notebook_id, "mode": format}, ctx)

    async def generate_podcast(self, notebook_id: str, language: str, ctx: Context) -> dict:
        # H 流程: 播客
        return await generate_run({"notebook_id": notebook_id, "mode": "podcast"}, ctx)

    async def configure_pipeline(self, pipeline_name: str, settings: Dict[str, Any], ctx: Context) -> dict:
        self.config.update(pipeline_name, settings)
        return {"status": "configured", "pipeline": pipeline_name, "settings": self.config.settings[pipeline_name]}
