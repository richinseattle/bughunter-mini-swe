"""A mini-swe-agent agent that dispatches tool calls to a ``ToolRegistry``.

The control flow is identical to ``InteractiveAgent`` (confirm / yolo / human modes); only
action execution changes: ``bash`` goes to the environment, everything else to the
registry (native function tools and MCP servers), with ``submit`` ending the run.
"""

from rich.console import Console

from minisweagent.agents.default import DefaultAgent
from minisweagent.agents.interactive import InteractiveAgent
from minisweagent.environments import Environment
from minisweagent.exceptions import Submitted
from minisweagent.models import Model

from .display import format_message
from .tools.registry import ToolRegistry

console = Console(highlight=False)


def _submission_output(e: Submitted) -> dict:
    """Observation for the action that terminated the run (instead of 'action was not executed')."""
    message = e.messages[-1] if e.messages else {}
    content = message.get("content") or message.get("extra", {}).get("submission", "")
    return {"output": content, "returncode": 0, "exception_info": "", "extra": {"submitted": True}}


class BughunterAgent(InteractiveAgent):
    def __init__(self, model: Model, env: Environment, *, tools: ToolRegistry, **kwargs) -> None:
        super().__init__(model, env, **kwargs)
        self.tools = tools

    def add_messages(self, *messages: dict) -> list[dict]:
        # Same console style as InteractiveAgent, but with tool names rendered.
        for message in messages:
            role = message.get("role") or message.get("type", "unknown")
            if role == "assistant":
                console.print(
                    f"\n[red][bold]bughunter[/bold] (step [bold]{self.n_calls}[/bold], [bold]${self.cost:.2f}[/bold]):[/red]\n",
                    end="",
                    highlight=False,
                )
            else:
                console.print(f"\n[bold green]{role.capitalize()}[/bold green]:\n", end="", highlight=False)
            console.print(format_message(message), highlight=False, markup=False)
        return DefaultAgent.add_messages(self, *messages)

    def execute_actions(self, message: dict) -> list[dict]:
        actions = message.get("extra", {}).get("actions", [])
        outputs = []
        try:
            self._ask_confirmation_or_interrupt([action.get("command", action.get("tool", "")) for action in actions])
            for action in actions:
                try:
                    outputs.append(self.tools.execute(action))
                except Submitted as e:
                    outputs.append(_submission_output(e))
                    raise
        except Submitted as e:
            self._check_for_new_task_or_submit(e)
        finally:
            result = self.add_messages(
                *self.model.format_observation_messages(message, outputs, self.get_template_vars())
            )
        return result
