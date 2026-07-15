from __future__ import annotations

import json
import sys

from .client import OnbAuthError, OnbError, OnbNotFoundError
from .hooks import EventHook, log
from .ir import Context
from .registry import OperatorRegistry

__version__ = "0.6.0"

_mcp = False


def _send(msg: dict) -> None:
    b = json.dumps(msg, ensure_ascii=False)
    if _mcp:
        sys.stdout.write(f"Content-Length: {len(b)}\r\n\r\n{b}")
    else:
        sys.stdout.write(b + "\n")
    sys.stdout.flush()


def _read() -> dict:
    global _mcp
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    if line.startswith("Content-Length:"):
        _mcp = True
        n = int(line.strip().split(": ")[1])
        sys.stdin.readline()
        return json.loads(sys.stdin.read(n))
    return json.loads(line)


from .pipeline_executor import PipelineExecutor

...

async def serve(registry: OperatorRegistry, pipeline_executor: PipelineExecutor, ctx: Context,
                hooks: list[EventHook]) -> None:
    import asyncio

    log("INFO", "notellm-mcp entering serve loop", version=__version__)

    async def _fire(method: str, *args: object) -> None:
        for hook in hooks:
            fn = getattr(hook, method, None)
            if fn:
                await fn(*args)

    loop = asyncio.get_running_loop()

    while True:
        try:
            req = await loop.run_in_executor(None, _read)
        except EOFError:
            log("INFO", "EOF on stdin, shutting down")
            break
        except json.JSONDecodeError as e:
            log("ERROR", "Invalid JSON on stdin", error=str(e))
            continue

        mid = req.get("id")
        method = req.get("method", "")
        params = req.get("params", {})

        try:
            if method == "initialize":
                client_version = params.get("protocolVersion", "unknown")
                log("INFO", "Client initialized", protocol_version=client_version)
                _send({
                    "jsonrpc": "2.0", "id": mid,
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "notellm-mcp", "version": __version__},
                    },
                })

            elif method in ("notifications/initialized", "notifications/cancelled"):
                pass

            elif method == "ping":
                _send({"jsonrpc": "2.0", "id": mid, "result": {}})

            elif method == "tools/list":
                tools = [
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
                _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}})

            elif method == "tools/call":
                tool = params.get("name", "")
                args = params.get("arguments", {})

                await _fire("on_tool_start", tool, args)

                try:
                    if tool == "collect":
                        result = await pipeline_executor.collect_data(**args, ctx=ctx)
                    elif tool == "summarize":
                        result = await pipeline_executor.summarize_notebook(**args, ctx=ctx)
                    elif tool == "podcast":
                        result = await pipeline_executor.generate_podcast(**args, ctx=ctx)
                    elif tool == "config":
                        result = await pipeline_executor.configure_pipeline(**args, ctx=ctx)
                    else:
                        raise KeyError(f"Unknown tool: {tool}")

                    await _fire("on_tool_complete", tool, result)
                    _send({
                        "jsonrpc": "2.0", "id": mid,
                        "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}]}
                    })
                except KeyError:
                    _send({
                        "jsonrpc": "2.0", "id": mid,
                        "error": {"code": -32601, "message": f"Unknown tool: {tool}"},
                    })
                except OnbAuthError as e:
                    await _fire("on_tool_error", tool, str(e))
                    _send({
                        "jsonrpc": "2.0", "id": mid,
                        "error": {
                            "code": -32001,
                            "message": f"ONB authentication failed: {e}. Set NOTELLM_ONB_PASSWORD.",
                        },
                    })
                except OnbNotFoundError as e:
                    _send({
                        "jsonrpc": "2.0", "id": mid,
                        "error": {"code": -32002, "message": str(e)},
                    })
                except OnbError as e:
                    await _fire("on_tool_error", tool, str(e))
                    _send({
                        "jsonrpc": "2.0", "id": mid,
                        "error": {"code": -32003, "message": str(e)},
                    })
                except (OSError, ValueError) as e:
                    await _fire("on_tool_error", tool, str(e))
                    _send({
                        "jsonrpc": "2.0", "id": mid,
                        "error": {"code": -32603, "message": f"Internal error: {e}"},
                    })
                except Exception as e:
                    await _fire("on_tool_error", tool, str(e))
                    _send({
                        "jsonrpc": "2.0", "id": mid,
                        "error": {"code": -32603, "message": f"Unexpected error: {e}"},
                    })

            else:
                _send({
                    "jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32601, "message": f"Unknown method: {method}"},
                })

        except Exception as e:
            log("ERROR", "Unhandled exception in main loop", error=str(e))
            try:
                _send({
                    "jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32603, "message": f"Server error: {e}"},
                })
            except Exception:
                pass
