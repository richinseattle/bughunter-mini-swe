"""Async bridge and FastMCP client wrapper.

FastMCP clients are async while the mini-swe-agent loop is synchronous. ``AsyncRunner``
owns a single event loop in a daemon thread so MCP sessions stay open across many
synchronous tool calls (calling ``asyncio.run`` per call would tear down stdio servers).
"""

import asyncio
import json
import re
import threading
from contextlib import AsyncExitStack
from typing import Any

from fastmcp import Client, FastMCP

_NAME_RE = re.compile(r"[^A-Za-z0-9_-]")
_MAX_NAME = 64

# Default to the JSON-RPC "legacy" handshake: newer FastMCP clients otherwise send a
# `server/discover` request that older MCP servers (e.g. mcp-server-fetch) reject.
DEFAULT_MODE = "legacy"


def sanitize_tool_name(name: str) -> str:
    """Map an arbitrary tool name onto the OpenAI function-name charset."""
    return _NAME_RE.sub("_", name)[:_MAX_NAME] or "tool"


class AsyncRunner:
    """Runs coroutines on one long-lived event loop in a background thread."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="bughunter-async", daemon=True)
        self._thread.start()

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


class McpClient:
    """A started MCP client exposing its tools under an explicit name prefix.

    ``transport`` is either an in-memory ``FastMCP`` server or a single-server config
    dict (``{"command": ..., "args": ...}`` / ``{"url": ...}``).
    """

    def __init__(
        self,
        transport,
        runner: AsyncRunner,
        *,
        name: str,
        prefix: str | None = None,
        mode: str = DEFAULT_MODE,
        include_tools: list[str] | None = None,
        exclude_tools: list[str] | None = None,
    ) -> None:
        self.name = name
        self.prefix = prefix if prefix is not None else f"{sanitize_tool_name(name)}__"
        self._client = Client({"mcpServers": {name: transport}} if not _is_server(transport) else transport, mode=mode)
        self._runner = runner
        self._include = set(include_tools or ())
        self._exclude = set(exclude_tools or ())
        self._stack: AsyncExitStack | None = None
        self._schemas: list[dict] = []
        self._tool_names: dict[str, str] = {}

    def start(self) -> "McpClient":
        self._stack = AsyncExitStack()
        self._runner.run(self._stack.enter_async_context(self._client))
        self._tool_names, self._schemas = {}, []
        for tool in self._runner.run(self._client.list_tools()):
            if self._include and tool.name not in self._include:
                continue
            if tool.name in self._exclude:
                continue
            exposed = self.prefix + sanitize_tool_name(tool.name)
            self._tool_names[exposed] = tool.name
            self._schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": exposed,
                        "description": tool.description or "",
                        "parameters": tool.input_schema or {"type": "object", "properties": {}},
                    },
                }
            )
        return self

    def schemas(self) -> list[dict]:
        return list(self._schemas)

    def call(self, name: str, arguments: dict) -> dict[str, Any]:
        result = self._runner.run(self._client.call_tool(self._tool_names[name], arguments))
        return {
            "output": _render_result(result),
            "returncode": 1 if result.is_error else 0,
            "exception_info": f"MCP tool '{name}' on server '{self.name}' returned an error" if result.is_error else "",
            "extra": {"tool": name, "mcp_server": self.name},
        }

    def close(self) -> None:
        if self._stack is not None:
            self._runner.run(self._stack.aclose())
            self._stack = None


def _is_server(transport) -> bool:
    return isinstance(transport, FastMCP)


def _render_result(result) -> str:
    parts = []
    for item in result.content or []:
        text = getattr(item, "text", None)
        parts.append(text if text is not None else json.dumps(item.model_dump(mode="json")))
    if not parts and result.structured_content is not None:
        parts.append(json.dumps(result.structured_content, indent=2))
    return "\n".join(parts)
