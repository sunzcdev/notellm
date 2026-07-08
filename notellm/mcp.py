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


async def serve(registry: OperatorRegistry, ctx: Context,
                hooks: list[EventHook]) -> None:
    import asyncio

    log("INFO", "notellm-mcp starting", version=__version__)

    async def _fire(method: str, *args: object) -> None:
        for hook in hooks:
            fn = getattr(hook, method, None)
            if fn:
                await fn(*args)

    while True:
        try:
            req = await asyncio.get_event_loop().run_in_executor(None, _read)
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
                _send({
                    "jsonrpc": "2.0", "id": mid,
                    "result": {"tools": registry.list_tools()},
                })

            elif method == "tools/call":
                tool = params.get("name", "")
                args = params.get("arguments", {})

                await _fire("on_tool_start", tool, args)

                try:
                    result = await registry.call_tool(tool, args, ctx)
                    await _fire("on_tool_complete", tool, result)
                    _send({
                        "jsonrpc": "2.0", "id": mid,
                        "result": {
                            "content": [
                                {"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}
                            ]
                        },
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
