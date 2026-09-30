"""Uniform tool plane: routes bash, submit, native Python tools and MCP tools.

Bash maps onto the ``Environment``. Native tools are plain (sync or async) Python callables
whose schemas are generated from their signatures; unlike MCP tools they can raise
``Submitted`` to end a run. MCP tools run through a started ``McpClient``.
"""

import inspect
import json
from typing import Any

from fastmcp.tools import FunctionTool

from minisweagent.exceptions import Submitted
from minisweagent.models.utils.actions_toolcall import BASH_TOOL

from .mcp import AsyncRunner, McpClient
from .policy import BinaryPolicy

SUBMIT_TOOL = {
    "type": "function",
    "function": {
        "name": "submit",
        "description": "Finish the task and submit your final summary. After this, the run ends.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Final summary of findings and changes made"},
            },
            "required": ["summary"],
        },
    },
}


class ToolRegistry:
    def __init__(
        self,
        env,
        runner: AsyncRunner,
        *,
        binary_policy: BinaryPolicy | None = None,
        include_bash: bool = True,
        include_submit: bool = True,
    ) -> None:
        self.env = env
        self.binary_policy = binary_policy
        self.include_bash = include_bash
        self.include_submit = include_submit
        self._runner = runner
        self._clients: dict[str, McpClient] = {}
        self._routes: dict[str, McpClient] = {}
        self._natives: dict[str, Any] = {}
        self._native_schemas: list[dict] = []
        self._cleanups: list = []

    def add_tool(self, fn, *, name: str | None = None, description: str | None = None) -> None:
        """Register a plain Python callable as a tool, generating its schema from the signature."""
        tool = FunctionTool.from_function(fn, name=name, description=description)
        self._natives[tool.name] = fn
        self._native_schemas.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.parameters,
                },
            }
        )

    def add_server(
        self,
        spec,
        *,
        name: str,
        mode: str = "legacy",
        prefix: str | None = None,
        include_tools: list[str] | None = None,
        exclude_tools: list[str] | None = None,
    ) -> None:
        """Start a server (in-memory ``FastMCP`` or a single-server config dict) and expose its tools."""
        client = McpClient(
            spec,
            self._runner,
            name=name,
            prefix=prefix,
            mode=mode,
            include_tools=include_tools,
            exclude_tools=exclude_tools,
        ).start()
        self._clients[name] = client
        for schema in client.schemas():
            self._routes[schema["function"]["name"]] = client

    def add_cleanup(self, cleanup) -> None:
        """Register a zero-argument callable run by :meth:`close` (e.g. to tear down SSH sessions)."""
        self._cleanups.append(cleanup)

    def schemas(self) -> list[dict]:
        fixed = ([BASH_TOOL] if self.include_bash else []) + ([SUBMIT_TOOL] if self.include_submit else [])
        return [*fixed, *self._native_schemas, *(s for client in self._clients.values() for s in client.schemas())]

    def tool_names(self) -> list[str]:
        return [schema["function"]["name"] for schema in self.schemas()]

    def execute(self, action: dict) -> dict[str, Any]:
        name = action.get("tool", "bash")
        if name == "bash":
            if not self.include_bash:
                return {"output": "'bash' is not available for this role.", "returncode": 1, "exception_info": ""}
            if self.binary_policy is not None and (denied := self.binary_policy.check(action.get("command", ""))):
                return {"output": denied, "returncode": 1, "exception_info": ""}
            return self.env.execute(action)  # may raise Submitted from _check_finished
        if name == "submit":
            summary = action.get("args", {}).get("summary", "")
            raise Submitted(
                {"role": "exit", "content": summary, "extra": {"exit_status": "Submitted", "submission": summary}}
            )
        if name in self._natives:
            return self._call_native(name, action.get("args", {}))
        client = self._routes.get(name)
        if client is None:
            available = ", ".join(sorted({*self._natives, *self._routes})) or "none"
            return {
                "output": f"Unknown tool '{name}'. Available tools: {available}",
                "returncode": 1,
                "exception_info": "",
            }
        try:
            return client.call(name, action.get("args", {}))
        except Exception as e:  # a failing tool is an observation for the model, not a crash
            return {"output": "", "returncode": -1, "exception_info": f"{type(e).__name__}: {e}"}

    def _call_native(self, name: str, arguments: dict) -> dict[str, Any]:
        try:
            result = self._natives[name](**arguments)
            if inspect.isawaitable(result):
                result = self._runner.run(result)
        except Submitted:
            raise
        except Exception as e:
            return {"output": "", "returncode": -1, "exception_info": f"{type(e).__name__}: {e}"}
        return {
            "output": result if isinstance(result, str) else json.dumps(result, default=str),
            "returncode": 0,
            "exception_info": "",
        }

    def close(self) -> None:
        for cleanup in self._cleanups:
            cleanup()
        for client in self._clients.values():
            client.close()


__all__ = ["SUBMIT_TOOL", "ToolRegistry"]
