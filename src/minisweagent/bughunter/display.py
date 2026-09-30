"""Human-readable console rendering of agent messages.

Starts from mini's ``get_content_string`` but renders tool calls with their name and
pretty-printed arguments, so native/MCP calls (e.g. ``update_password``) are readable.
"""

import json

from minisweagent.models.utils.content_string import get_content_string


def _format_tool_call(name: str, arguments) -> str:
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except json.JSONDecodeError:
        args = {}
    if isinstance(args, dict) and "command" in args:
        body = f"```\n{args['command']}\n```"
    else:
        body = f"```json\n{json.dumps(args, indent=2, default=str)}\n```"
    return f"**{name}**\n{body}"


def format_message(message: dict) -> str:
    """Render a message for the console, including tool-call names and arguments."""
    if not (tool_calls := message.get("tool_calls")):
        return get_content_string(message)
    parts = []
    if isinstance(content := message.get("content"), str) and content.strip():
        parts.append(content)
    for call in tool_calls:
        function = call.get("function", {}) if isinstance(call, dict) else {}
        parts.append(_format_tool_call(function.get("name", "?"), function.get("arguments", "{}")))
    return "\n\n".join(parts)
