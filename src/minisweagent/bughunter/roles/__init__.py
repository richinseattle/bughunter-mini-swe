"""Role registry. Roles are imported lazily so optional dependencies (e.g. asyncssh for
OverTheWire) are only required when that role is selected.
"""

from .base import AgentRole

ROLE_DESCRIPTIONS = {
    "generic": "General-purpose software engineering agent.",
    "overthewire": "Solve OverTheWire Bandit levels over SSH, advancing automatically.",
    "recon": "Network reconnaissance using the `recon` MCP server group.",
}


def get_role(name: str) -> AgentRole:
    if name == "generic":
        return AgentRole()
    if name == "overthewire":
        from .overthewire import OverTheWireRole

        return OverTheWireRole()
    if name == "recon":
        from .recon import ReconRole

        return ReconRole()
    raise ValueError(f"Unknown role '{name}'. Available: {sorted(ROLE_DESCRIPTIONS)}")


def role_names() -> list[str]:
    return sorted(ROLE_DESCRIPTIONS)


__all__ = ["AgentRole", "ROLE_DESCRIPTIONS", "get_role", "role_names"]
