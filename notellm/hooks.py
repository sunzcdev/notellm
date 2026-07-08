from __future__ import annotations

import json
import sys
import time


class EventHook:
    async def on_tool_start(self, tool_name: str, args: dict) -> None:
        pass

    async def on_tool_complete(self, tool_name: str, result: dict) -> None:
        pass

    async def on_tool_error(self, tool_name: str, error: str) -> None:
        pass


class StderrLogger(EventHook):
    def _log(self, level: str, msg: str, **ctx) -> None:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        entry = {"ts": ts, "level": level, "msg": msg, "ctx": ctx}
        sys.stderr.write(json.dumps(entry, ensure_ascii=False) + "\n")
        sys.stderr.flush()

    async def on_tool_start(self, tool_name: str, args: dict) -> None:
        self._log("DEBUG", "Tool called", tool=tool_name)

    async def on_tool_complete(self, tool_name: str, result: dict) -> None:
        self._log("DEBUG", "Tool completed", tool=tool_name,
                  status=result.get("status", "unknown"))

    async def on_tool_error(self, tool_name: str, error: str) -> None:
        self._log("ERROR", "Tool error", tool=tool_name, detail=error)


def log(level: str, msg: str, **ctx) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    entry = {"ts": ts, "level": level, "msg": msg, "ctx": ctx}
    sys.stderr.write(json.dumps(entry, ensure_ascii=False) + "\n")
    sys.stderr.flush()
