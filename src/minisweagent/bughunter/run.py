#!/usr/bin/env python3

"""Bughunter: mini-swe-agent extended with local function and MCP tool calling.

All three invocation styles work::

    bughunter -t "..."                       # installed console script
    python -m minisweagent.bughunter -t "..." # as a module
    python run.py -t "..."                    # directly from this directory

Configuration is layered (later wins): packaged defaults, the global user config
(``~/.config/mini-swe-agent/bughunter.yaml``), the project-local ``./bughunter.yaml``,
``-c`` specs, then CLI flags. LLM service endpoints live in a separate ``llm.yaml``
(global and/or ``./llm.yaml``). ``.env`` files are loaded by default.
"""

if __package__ in (None, ""):
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from minisweagent import global_config_dir
from minisweagent.agents.utils.prompt_user import _multiline_prompt
from minisweagent.bughunter.agent import BughunterAgent
from minisweagent.bughunter.model import BughunterModel
from minisweagent.bughunter.settings import (
    load_bughunter_config,
    load_env_files,
    load_llm_config,
    pick_service,
    resolve_mcp_servers,
    resolve_service,
)
from minisweagent.bughunter.tools.functions import build_local_server
from minisweagent.bughunter.tools.mcp import AsyncRunner
from minisweagent.bughunter.tools.policy import BinaryPolicy
from minisweagent.bughunter.tools.registry import ToolRegistry
from minisweagent.environments import get_environment
from minisweagent.models import get_model_name
from minisweagent.run.utilities.config import configure_if_first_time
from minisweagent.utils.serialize import UNSET, recursive_merge

DEFAULT_CONFIG_FILE = Path(__file__).parent / "config" / "bughunter.yaml"
DEFAULT_OUTPUT_FILE = global_config_dir / "last_bughunter_run.traj.json"

console = Console(highlight=False)
app = typer.Typer(rich_markup_mode="rich")


def _build_registry(config: dict, env, runner: AsyncRunner) -> ToolRegistry:
    tools = config.get("tools", {})
    registry = ToolRegistry(env, runner, binary_policy=BinaryPolicy.from_config(tools.get("binary_policy", {})))
    if tools.get("functions", True):
        registry.add_server(build_local_server(), name="local")
    ssh_config = tools.get("ssh", {})
    if ssh_config.get("enabled", False):
        from minisweagent.bughunter.tools.ssh import SshManager, build_ssh_server

        manager = SshManager(
            known_hosts=ssh_config.get("known_hosts"),
            connect_timeout=ssh_config.get("connect_timeout", 30.0),
            timeout=ssh_config.get("timeout", 30.0),
        )
        registry.add_server(build_ssh_server(manager, ssh_config.get("hosts", {})), name="ssh", prefix="")
        registry.add_cleanup(lambda: runner.run(manager.aclose_all()))
    for server in resolve_mcp_servers(tools):
        name = server.pop("name")
        registry.add_server(
            server,
            name=name,
            mode=server.pop("mode", "legacy"),
            prefix=server.pop("prefix", None),
            include_tools=server.pop("include_tools", None),
            exclude_tools=server.pop("exclude_tools", None),
        )
    return registry


def _load_config(config_spec: list[str], overrides: dict) -> dict:
    return load_bughunter_config(config_spec, overrides, DEFAULT_CONFIG_FILE)


# fmt: off
@app.command()
def main(
    task: str | None = typer.Option(None, "-t", "--task", help="Task/problem statement", show_default=False),
    model_name: str | None = typer.Option(None, "-m", "--model", help="Model to use"),
    service: str | None = typer.Option(None, "--service", help="Named LLM service from llm.yaml"),
    llm_config: list[Path] = typer.Option([], "-L", "--llm-config", help="Extra llm.yaml file(s)"),
    mcp_group: list[str] = typer.Option([], "--mcp-group", help="Enable an MCP server group from config"),
    mcp_server: list[str] = typer.Option([], "--mcp-server", help="Enable an MCP server from the catalog"),
    config_spec: list[str] = typer.Option([str(DEFAULT_CONFIG_FILE)], "-c", "--config", help="Config files or key=value overrides"),
    yolo: bool = typer.Option(False, "-y", "--yolo", help="Run without confirmation"),
    cost_limit: float | None = typer.Option(None, "-l", "--cost-limit", help="Cost limit. Set to 0 to disable."),
    output: Path | None = typer.Option(DEFAULT_OUTPUT_FILE, "-o", "--output", help="Output trajectory file"),
    list_tools: bool = typer.Option(False, "--list-tools", help="List available tools and exit", rich_help_panel="Advanced"),
) -> Any:
    # fmt: on
    configure_if_first_time()
    load_env_files(*[Path(spec).resolve().parent for spec in config_spec if "=" not in spec])
    config = _load_config(config_spec, {
        "run": {"task": task or UNSET},
        "agent": {
            "mode": "yolo" if yolo else UNSET,
            "cost_limit": cost_limit if cost_limit is not None else UNSET,
            "output_path": output or UNSET,
        },
        "model": {"model_name": model_name or UNSET},
    })
    if mcp_group or mcp_server:
        tools = config.setdefault("tools", {})
        tools["mcp_servers"] = [*tools.get("mcp_servers", []), *mcp_group, *mcp_server]

    env = get_environment(config.get("environment", {}), default_type="local")
    runner = AsyncRunner()
    registry = None
    try:
        registry = _build_registry(config, env, runner)
        if list_tools:
            for tool in registry.schemas():
                function = tool["function"]
                description = function["description"].splitlines()[0] if function["description"] else ""
                console.print(f"[bold green]{function['name']}[/bold green]: {description}")
            return registry
        model_config = config.get("model", {})
        api_key = model_config.pop("api_key", None)
        llm = load_llm_config(llm_config)
        if selected := pick_service(llm, service):
            resolved, api_key = resolve_service(llm, selected)
            model_config = recursive_merge(model_config, resolved)
            console.print(f"Using LLM service [bold green]{selected}[/bold green]")
        model_config["model_name"] = get_model_name(model_name, model_config)
        model = BughunterModel(tool_schemas=registry.schemas(), api_key=api_key, **model_config)
        agent = BughunterAgent(model, env, tools=registry, **config.get("agent", {}))
        run_task = config.get("run", {}).get("task", UNSET)
        if run_task is UNSET:
            console.print("[bold yellow]What do you want to do?")
            run_task = _multiline_prompt()
        agent.run(run_task)
        if output_path := config.get("agent", {}).get("output_path"):
            console.print(f"Saved trajectory to [bold green]'{output_path}'[/bold green]")
        return agent
    finally:
        if registry is not None:
            registry.close()
        runner.close()


if __name__ == "__main__":
    app()
