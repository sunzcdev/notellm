import asyncio
import json
from unittest.mock import AsyncMock
from notellm.mcp import serve
from notellm.pipeline_executor import PipelineExecutor
from notellm.registry import OperatorRegistry
from notellm.ir import Context

# Mock classes
class MockRegistry:
    def list_tools(self): return []
class MockContext:
    def __init__(self):
        self.config = type('obj', (object,), {
            'obsidian_vault': '/tmp',
            'max_file_size': 1024 * 1024
        })()
        self.client = AsyncMock()
        self.client.get = AsyncMock(return_value=[{"id": "t1", "name": "briefing"}])
        self.client.post = AsyncMock(return_value={"id": "sid1", "output": "test summary"})


async def test_mcp_routing():
    registry = MockRegistry()
    executor = PipelineExecutor(registry)
    ctx = MockContext()
    
    # Simulate a tool call through the new routing logic (we test logic flow via serve function simulation)
    # We will invoke the pipeline_executor directly as mcp.py would
    print("Testing pipeline executor routing...")
    
    # Test 1: collect_data
    # Create a dummy file for the test
    with open("/tmp/test.md", "w") as f:
        f.write("--- title: Test ---\nThis is a long enough content to pass validation.")
    res1 = await executor.collect_data("AI", "/tmp/test.md", "url", ctx)
    print(f"Collect result: {res1}")
    
    # Test 2: summarize_notebook
    res2 = await executor.summarize_notebook("nb1", "briefing", ctx)
    print(f"Summarize result: {res2}")
    
    # Test 3: configure_pipeline
    res3 = await executor.configure_pipeline("collect", {"timeout": 600}, ctx)
    print(f"Configure result: {res3}")

    assert res1["source_id"] == "sid1"
    assert res2["output"] == "test summary"
    assert res3["settings"]["timeout"] == 600
    print("MCP routing integration test passed.")

if __name__ == "__main__":
    asyncio.run(test_mcp_routing())
