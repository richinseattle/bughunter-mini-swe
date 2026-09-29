"""Uniform tool plane: routes bash, submit and MCP/function tools.

Bash is special because it maps onto the ``Environment`` and preserves the
``COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`` convention. Everything else (local Python
functions and external MCP servers) goes through the same MCP client path.
"""

from typing import Any

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
    def __init__(self, env, runner: AsyncRunner, *, binary_policy: BinaryPolicy | None = None) -> None:
        self.env = env
        self.binary_policy = binary_policy
        self._runner = runner
        self._clients: dict[str, McpClient] = {}
        self._routes: dict[str, McpClient] = {}
        self._cleanups: list = []

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
        return [BASH_TOOL, SUBMIT_TOOL, *(schema for client in self._clients.values() for schema in client.schemas())]

    def tool_names(self) -> list[str]:
        return [schema["function"]["name"] for schema in self.schemas()]

    def execute(self, action: dict) -> dict[str, Any]:
        name = action.get("tool", "bash")
        if name == "bash":
            if self.binary_policy is not None and (denied := self.binary_policy.check(action.get("command", ""))):
                return {"output": denied, "returncode": 1, "exception_info": ""}
            return self.env.execute(action)  # may raise Submitted from _check_finished
        if name == "submit":
            summary = action.get("args", {}).get("summary", "")
            raise Submitted(
                {"role": "exit", "content": summary, "extra": {"exit_status": "Submitted", "submission": summary}}
            )
        client = self._routes.get(name)
        if client is None:
            available = ", ".join(sorted(self._routes)) or "none"
            return {
                "output": f"Unknown tool '{name}'. Available tools: {available}",
                "returncode": 1,
                "exception_info": "",
            }
        try:
            return client.call(name, action.get("args", {}))
        except Exception as e:  # a failing tool is an observation for the model, not a crash
            return {"output": "", "returncode": -1, "exception_info": f"{type(e).__name__}: {e}"}

    def close(self) -> None:
        for cleanup in self._cleanups:
            cleanup()
        for client in self._clients.values():
            client.close()


__all__ = ["SUBMIT_TOOL", "ToolRegistry"]
