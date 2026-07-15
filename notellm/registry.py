from __future__ import annotations

import importlib.util
import os
import sys
from types import ModuleType
from typing import Any

from .ir import Context


class MissingOperator:
    def __init__(self, name: str, error: str):
        self.name = name
        self.error = error


class OperatorRegistry:
    def __init__(self) -> None:
        self._operators: dict[str, ModuleType | MissingOperator] = {}

    def discover(self, operator_dir: str) -> None:
        if not os.path.isdir(operator_dir):
            return
        for filename in sorted(os.listdir(operator_dir)):
            if not filename.endswith(".py") or filename.startswith("_"):
                continue
            name = filename[:-3]
            filepath = os.path.join(operator_dir, filename)
            self._load(name, filepath)

    def _load(self, name: str, filepath: str) -> None:
        try:
            spec = importlib.util.spec_from_file_location(f"operators.{name}", filepath)
            if spec is None or spec.loader is None:
                self._operators[name] = MissingOperator(name, f"cannot load {filepath}")
                return
            module = importlib.util.module_from_spec(spec)
            sys.modules[f"operators.{name}"] = module
            spec.loader.exec_module(module)
            self._operators[name] = module
        except Exception as e:
            self._operators[name] = MissingOperator(name, str(e))

    def register(self, name: str, module: ModuleType) -> None:
        self._operators[name] = module

    def get(self, name: str) -> ModuleType | MissingOperator | None:
        return self._operators.get(name)

    def is_missing(self, name: str) -> bool:
        op = self._operators.get(name)
        return op is None or isinstance(op, MissingOperator)

    def get_error(self, name: str) -> str | None:
        op = self._operators.get(name)
        if isinstance(op, MissingOperator):
            return op.error
        if op is None:
            return f"operator '{name}' not found"
        return None

    @property
    def names(self) -> list[str]:
        return list(self._operators.keys())

    def list_tools(self) -> list[dict]:
        return [
            {
                "name": "collect",
                "description": "采集数据并入库",
                "inputSchema": {"type": "object", "properties": {"topic": {"type": "string"}, "source_input": {"type": "string"}, "source_type": {"type": "string"}}, "required": ["topic", "source_input", "source_type"]}
            },
            {
                "name": "summarize",
                "description": "总结 Notebook 内容",
                "inputSchema": {"type": "object", "properties": {"notebook_id": {"type": "string"}, "format": {"type": "string"}}, "required": ["notebook_id", "format"]}
            },
            {
                "name": "podcast",
                "description": "生成播客",
                "inputSchema": {"type": "object", "properties": {"notebook_id": {"type": "string"}, "language": {"type": "string"}}, "required": ["notebook_id", "language"]}
            },
            {
                "name": "config",
                "description": "调整运行时参数",
                "inputSchema": {"type": "object", "properties": {"pipeline_name": {"type": "string"}, "settings": {"type": "object"}}, "required": ["pipeline_name", "settings"]}
            }
        ]

    async def call_tool(self, name: str, args: dict, ctx: Context) -> dict[str, Any]:
        tool_name = name
        for op_name, op in self._operators.items():
            if isinstance(op, MissingOperator):
                continue
            schema = getattr(op, "TOOL_SCHEMA", None)
            if schema and schema.get("name") == name:
                tool_name = op_name
                break

        op = self._operators.get(tool_name)
        if op is None or isinstance(op, MissingOperator):
            raise KeyError(f"Unknown tool: {name}")

        run_fn = getattr(op, "run", None)
        if run_fn is None:
            raise KeyError(f"Operator '{tool_name}' has no run function")

        return await run_fn(args, ctx)
