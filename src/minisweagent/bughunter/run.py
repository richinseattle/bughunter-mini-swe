#!/usr/bin/env python3

"""Bughunter: mini-swe-agent extended with local function and MCP tool calling.

All three invocation styles work::

    bughunter -t "..."                       # installed console script
    python -m minisweagent.bughunter -t "..." # as a module
    python run.py -t "..."                    # directly from this directory
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
from minisweagent.bughunter.tools.functions import build_local_server
from minisweagent.bughunter.tools.mcp import AsyncRunner
from minisweagent.bughunter.tools.registry import ToolRegistry
from minisweagent.config import get_config_from_spec
from minisweagent.environments import get_environment
from minisweagent.models import get_model_name
from minisweagent.run.utilities.config import configure_if_first_time
from minisweagent.utils.serialize import UNSET, recursive_merge

DEFAULT_CONFIG_FILE = Path(__file__).parent / "config" / "bughunter.yaml"
DEFAULT_OUTPUT_FILE = global_config_dir / "last_bughunter_run.traj.json"

console = Console(highlight=False)
app = typer.Typer(rich_markup_mode="rich")


def _build_registry(config: dict, env, runner: AsyncRunner) -> ToolRegistry:
    registry = ToolRegistry(env, runner)
    tools = config.get("tools", {})
    if tools.get("functions", True):
        registry.add_server(build_local_server(), name="local")
    for server in tools.get("mcp_servers", []):
        name = server["name"]
        spec = {key: value for key, value in server.items() if key not in {"name", "mode", "prefix"}}
        registry.add_server(spec, name=name, mode=server.get("mode", "legacy"), prefix=server.get("prefix"))
    return registry


# fmt: off
@app.command()
def main(
    task: str | None = typer.Option(None, "-t", "--task", help="Task/problem statement", show_default=False),
    model_name: str | None = typer.Option(None, "-m", "--model", help="Model to use"),
    config_spec: list[str] = typer.Option([str(DEFAULT_CONFIG_FILE)], "-c", "--config", help="Config files or key=value overrides"),
    yolo: bool = typer.Option(False, "-y", "--yolo", help="Run without confirmation"),
    cost_limit: float | None = typer.Option(None, "-l", "--cost-limit", help="Cost limit. Set to 0 to disable."),
    output: Path | None = typer.Option(DEFAULT_OUTPUT_FILE, "-o", "--output", help="Output trajectory file"),
    list_tools: bool = typer.Option(False, "--list-tools", help="List available tools and exit", rich_help_panel="Advanced"),
) -> Any:
    # fmt: on
    configure_if_first_time()
    configs = [get_config_from_spec(spec) for spec in config_spec]
    configs.append({
        "run": {"task": task or UNSET},
        "agent": {
            "mode": "yolo" if yolo else UNSET,
            "cost_limit": cost_limit if cost_limit is not None else UNSET,
            "output_path": output or UNSET,
        },
        "model": {"model_name": model_name or UNSET},
    })
    config = recursive_merge(*configs)

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
        model_config["model_name"] = get_model_name(model_name, model_config)
        model = BughunterModel(tool_schemas=registry.schemas(), **model_config)
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
