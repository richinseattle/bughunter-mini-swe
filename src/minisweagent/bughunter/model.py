"""A Litellm model that advertises and parses an arbitrary set of tools."""

import json
from collections.abc import Callable

import litellm
from jinja2 import StrictUndefined, Template

from minisweagent.exceptions import FormatError
from minisweagent.models.litellm_model import LitellmModel, LitellmModelConfig


class BughunterModelConfig(LitellmModelConfig):
    pass


class BughunterModel(LitellmModel):
    """Like ``LitellmModel``, but ``tools`` come from the tool registry instead of a hardcoded ``bash``."""

    def __init__(
        self, *, tool_schemas: list[dict] | None = None, config_class: Callable = BughunterModelConfig, **kwargs
    ) -> None:
        super().__init__(config_class=config_class, **kwargs)
        self._tool_schemas = list(tool_schemas or [])

    def _query(self, messages: list[dict[str, str]], **kwargs):
        try:
            return litellm.completion(
                model=self.config.model_name,
                messages=messages,
                tools=self._tool_schemas,
                **(self.config.model_kwargs | kwargs),
            )
        except litellm.exceptions.AuthenticationError as e:
            e.message += " You can permanently set your API key with `mini-extra config set KEY VALUE`."
            raise e

    def _parse_actions(self, response) -> list[dict]:
        return parse_tool_calls(
            response.choices[0].message.tool_calls or [],
            format_error_template=self.config.format_error_template,
            template_kwargs={"finish_reason": response.choices[0].finish_reason},
        )


def _format_error(format_error_template: str, **kwargs) -> FormatError:
    return FormatError(
        {
            "role": "user",
            "content": Template(format_error_template, undefined=StrictUndefined).render(**kwargs),
            "extra": {"interrupt_type": "FormatError"},
        }
    )


def parse_tool_calls(
    tool_calls: list, *, format_error_template: str, template_kwargs: dict | None = None
) -> list[dict]:
    """Parse OpenAI-style tool calls into generic actions ``{"tool", "args", "command", "tool_call_id"}``.

    Unlike the core ``bash``-only parser, any tool name is accepted; unknown tools are
    reported back to the model by the registry at execution time.
    """
    template_kwargs = template_kwargs or {}
    if not tool_calls:
        raise _format_error(
            format_error_template,
            error="No tool calls found in the response. Every response MUST include at least one tool call.",
            actions=[],
            has_tool_calls=False,
            **template_kwargs,
        )
    actions = []
    for tool_call in tool_calls:
        name = tool_call.function.name
        try:
            args = json.loads(tool_call.function.arguments or "{}")
        except json.JSONDecodeError as e:
            raise _format_error(
                format_error_template,
                error=f"Error parsing arguments for tool '{name}': {e}.",
                actions=[],
                has_tool_calls=True,
                **template_kwargs,
            )
        if not isinstance(args, dict):
            raise _format_error(
                format_error_template,
                error=f"Arguments for tool '{name}' must be a JSON object.",
                actions=[],
                has_tool_calls=True,
                **template_kwargs,
            )
        if name == "bash" and "command" not in args:
            raise _format_error(
                format_error_template,
                error="Missing 'command' argument in bash tool call.",
                actions=[],
                has_tool_calls=True,
                **template_kwargs,
            )
        # `command` is kept so InteractiveAgent confirmation and content rendering keep working.
        actions.append({"tool": name, "args": args, "command": args.get("command", name), "tool_call_id": tool_call.id})
    return actions
