"""Network reconnaissance role.

Wraps the ``recon`` MCP server group (Shodan and friends) with a small findings store so
the agent can record and report what it discovers. Configure the servers in the global
config's ``mcp_server_catalog``/``mcp_server_groups``; the role enables the ``recon`` group.
"""

from dataclasses import dataclass, field

from minisweagent.bughunter.roles.base import AgentRole
from minisweagent.bughunter.tools.mcp import AsyncRunner
from minisweagent.bughunter.tools.registry import ToolRegistry

RECON_SYSTEM_PROMPT = """\
You are a network reconnaissance analyst. You investigate hosts, services and exposures
using the provided reconnaissance tools (for example Shodan search and host lookups) plus
whatever local tools you are given.

Work methodically: scope the target, enumerate, record every finding with `save_finding`,
and finish with a concrete report. Only act within the authorized scope. Never take
destructive action against a target.
"""


@dataclass
class ReconState:
    target: str = ""
    findings: dict[str, str] = field(default_factory=dict)
    pins: list[str] = field(default_factory=list)
    max_pins: int = 10

    def report(self) -> str:
        lines = [f"Target: {self.target or '(not set)'}"]
        lines += [f"- {key}: {value}" for key, value in self.findings.items()] or ["No findings yet."]
        return "\n".join(lines)


class ReconRole(AgentRole):
    name = "recon"
    description = "Network reconnaissance using the `recon` MCP server group."

    def __init__(self) -> None:
        self.state = ReconState()

    def configure(self) -> dict:
        return {
            "agent": {"system_template": RECON_SYSTEM_PROMPT, "instance_template": "{{task}}"},
            "tools": {
                "functions": False,
                "ssh": {"enabled": False},
                "mcp_servers": ["recon"],
                "binary_policy": {"mode": "blocklist"},
            },
        }

    def add_tools(self, registry: ToolRegistry, config: dict, runner: AsyncRunner) -> None:
        state = self.state
        if target := config.get("role", {}).get("target"):
            state.target = target

        def set_target(target: str) -> str:
            """Set the reconnaissance target (host, domain, IP range or organization)."""
            state.target = target
            return f"Target set to {target}."

        def save_finding(key: str, detail: str) -> str:
            """Save a finding under a short key so it appears in the final report."""
            state.findings[key] = detail
            return f"Saved finding '{key}'."

        def recall_finding(key: str) -> str:
            """Recall a saved finding by key."""
            return state.findings.get(key, "Not found.")

        def delete_finding(key: str) -> str:
            """Delete a saved finding by key."""
            state.findings.pop(key, None)
            return f"Deleted finding '{key}'."

        def list_findings() -> str:
            """List all saved findings and the current target."""
            return state.report()

        def pin_to_top(content: str) -> str:
            """Pin important content so it stays visible in the context."""
            state.pins.append(content)
            state.pins[:] = state.pins[-state.max_pins :]
            return "Pinned."

        for fn in (set_target, save_finding, recall_finding, delete_finding, list_findings, pin_to_top):
            registry.add_tool(fn)

    def run(self, agent, config: dict) -> dict:
        task = config.get("run", {}).get("task", "")
        if not task:
            task = f"Perform authorized reconnaissance on {self.state.target or 'the target'} and report your findings."
        return agent.run(task)
