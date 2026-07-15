import asyncio
from notellm.pipeline_executor import PipelineExecutor
from notellm.registry import OperatorRegistry
from notellm.ir import Context

class MockContext:
    def __init__(self):
        self.config = type('obj', (object,), {'obsidian_vault': '/tmp'})

async def test_router():
    registry = OperatorRegistry()
    executor = PipelineExecutor(registry)
    ctx = MockContext()
    
    # Verify Intent-based Interface Availability
    print("Testing pipeline router...")
    
    res1 = await executor.collect_data("AI", "http://test.com", "url", ctx)
    print(f"Collect result: {res1}")
    
    res2 = await executor.summarize_notebook("nb1", "briefing", ctx)
    print(f"Summarize result: {res2}")
    
    res3 = await executor.configure_pipeline("collect", {"timeout": 600}, ctx)
    print(f"Configure result: {res3}")
    
    assert res1["status"] == "collecting"
    assert res3["settings"]["timeout"] == 600
    print("All pipeline router tests passed.")

if __name__ == "__main__":
    asyncio.run(test_router())
