"""A mini-swe-agent agent that dispatches tool calls to a ``ToolRegistry``.

The control flow is identical to ``InteractiveAgent`` (confirm / yolo / human modes); only
action execution changes: ``bash`` goes to the environment, everything else to the
registry (local Python functions and MCP servers), with ``submit`` ending the run.
"""

from minisweagent.agents.interactive import InteractiveAgent
from minisweagent.environments import Environment
from minisweagent.exceptions import Submitted
from minisweagent.models import Model

from .tools.registry import ToolRegistry


class BughunterAgent(InteractiveAgent):
    def __init__(self, model: Model, env: Environment, *, tools: ToolRegistry, **kwargs) -> None:
        super().__init__(model, env, **kwargs)
        self.tools = tools

    def execute_actions(self, message: dict) -> list[dict]:
        actions = message.get("extra", {}).get("actions", [])
        outputs = []
        try:
            self._ask_confirmation_or_interrupt([action.get("command", action.get("tool", "")) for action in actions])
            for action in actions:
                outputs.append(self.tools.execute(action))
        except Submitted as e:
            self._check_for_new_task_or_submit(e)
        finally:
            result = self.add_messages(
                *self.model.format_observation_messages(message, outputs, self.get_template_vars())
            )
        return result
