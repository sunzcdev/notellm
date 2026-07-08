import os
import sys
import types
import pytest
from notellm.registry import OperatorRegistry, MissingOperator
from notellm.ir import OnbConfig, Context
from notellm.client import OnbClient


@pytest.fixture
def ctx():
    config = OnbConfig()
    client = OnbClient(config)
    return Context(client=client, config=config)


def test_discover_operators(tmp_path):
    op_file = tmp_path / "greet.py"
    op_file.write_text('''
TOOL_SCHEMA = {
    "name": "greet",
    "description": "Say hello",
    "inputSchema": {"type": "object", "properties": {}},
}

async def run(args, ctx):
    return {"message": "hello"}
''')

    reg = OperatorRegistry()
    reg.discover(str(tmp_path))
    assert "greet" in reg.names
    assert not reg.is_missing("greet")


def test_discover_skips_underscore(tmp_path):
    (tmp_path / "_internal.py").write_text("x = 1")
    (tmp_path / "visible.py").write_text("""
TOOL_SCHEMA = {"name": "visible", "description": "", "inputSchema": {"type": "object"}}
async def run(args, ctx): return {}
""")
    reg = OperatorRegistry()
    reg.discover(str(tmp_path))
    assert "visible" in reg.names
    assert "_internal" not in reg.names


def test_discover_bad_file(tmp_path):
    (tmp_path / "broken.py").write_text("raise RuntimeError('boom')")
    reg = OperatorRegistry()
    reg.discover(str(tmp_path))
    assert reg.is_missing("broken")
    assert "boom" in reg.get_error("broken")


def test_list_tools(tmp_path):
    (tmp_path / "a.py").write_text("""
TOOL_SCHEMA = {"name": "alpha", "description": "A", "inputSchema": {"type": "object"}}
async def run(args, ctx): return {"ok": True}
""")
    (tmp_path / "b.py").write_text("""
TOOL_SCHEMA = {"name": "beta", "description": "B", "inputSchema": {"type": "object"}}
async def run(args, ctx): return {"ok": True}
""")
    reg = OperatorRegistry()
    reg.discover(str(tmp_path))
    tools = reg.list_tools()
    names = [t["name"] for t in tools]
    assert "alpha" in names
    assert "beta" in names


@pytest.mark.asyncio
async def test_call_tool(tmp_path, ctx):
    (tmp_path / "echo.py").write_text("""
TOOL_SCHEMA = {"name": "echo", "description": "Echo", "inputSchema": {"type": "object"}}
async def run(args, ctx):
    return {"echoed": args.get("msg", "")}
""")
    reg = OperatorRegistry()
    reg.discover(str(tmp_path))
    result = await reg.call_tool("echo", {"msg": "hi"}, ctx)
    assert result == {"echoed": "hi"}


@pytest.mark.asyncio
async def test_call_unknown_tool(ctx):
    reg = OperatorRegistry()
    with pytest.raises(KeyError, match="Unknown tool"):
        await reg.call_tool("nonexistent", {}, ctx)


def test_register_manual():
    mod = types.ModuleType("manual_op")
    mod.TOOL_SCHEMA = {"name": "manual", "description": "M", "inputSchema": {"type": "object"}}
    async def _run(args, ctx):
        return {"manual": True}
    mod.run = _run

    reg = OperatorRegistry()
    reg.register("manual", mod)
    assert "manual" in reg.names
    assert not reg.is_missing("manual")
    tools = reg.list_tools()
    assert any(t["name"] == "manual" for t in tools)


def test_missing_operator_error():
    reg = OperatorRegistry()
    assert reg.is_missing("ghost")
    assert "not found" in reg.get_error("ghost")
