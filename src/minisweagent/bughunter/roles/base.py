"""Agent roles: modular bundles of prompt, tools and a run loop.

A role is selected with ``--role`` (or ``BUGHUNTER_ROLE``). It contributes a low-priority
config preset (overridable by global/local/``-c``/CLI), can register role-specific tools,
and controls how the agent is run (a single task, or a level-by-level loop).
"""

from minisweagent.bughunter.tools.mcp import AsyncRunner
from minisweagent.bughunter.tools.registry import ToolRegistry


class AgentRole:
    name = "generic"
    description = "General-purpose software engineering agent."

    def configure(self) -> dict:
        """Config overrides for this role, merged below global/local/``-c``/CLI layers."""
        return {}

    def add_tools(self, registry: ToolRegistry, config: dict, runner: AsyncRunner) -> None:
        """Register role-specific native tools (optional)."""

    def run(self, agent, config: dict) -> dict:
        """Run the agent. Default: a single task taken from the config."""
        return agent.run(config.get("run", {}).get("task", ""))
