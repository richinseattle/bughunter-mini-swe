from types import SimpleNamespace

import pytest

from minisweagent.bughunter.model import parse_tool_calls
from minisweagent.bughunter.tools.mcp import sanitize_tool_name
from minisweagent.exceptions import FormatError, Submitted

fastmcp = pytest.importorskip("fastmcp")


def tool_call(name: str, arguments: str, call_id: str = "call_1") -> SimpleNamespace:
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


@pytest.mark.parametrize(
    ("tool_calls", "expected"),
    [
        ([tool_call("bash", '{"command": "ls"}')], [("bash", "ls")]),
        ([tool_call("fetch__fetch", '{"url": "https://example.com"}')], [("fetch__fetch", "fetch__fetch")]),
    ],
)
def test_parse_tool_calls_accepts_any_tool(tool_calls, expected):
    actions = parse_tool_calls(tool_calls, format_error_template="{{error}}")
    assert [(a["tool"], a["command"]) for a in actions] == expected
    assert actions[0]["tool_call_id"] == "call_1"


@pytest.mark.parametrize(
    ("tool_calls", "message"),
    [
        ([], "No tool calls"),
        ([tool_call("bash", '{"nope": 1}')], "Missing 'command'"),
        ([tool_call("bash", "not json")], "Error parsing"),
        ([tool_call("bash", "[1, 2]")], "must be a JSON object"),
    ],
)
def test_parse_tool_calls_reports_format_errors(tool_calls, message):
    with pytest.raises(FormatError) as excinfo:
        parse_tool_calls(tool_calls, format_error_template="{{error}}")
    assert message in excinfo.value.messages[0]["content"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("fetch", "fetch"), ("my.tool/v2", "my_tool_v2"), ("", "tool")],
)
def test_sanitize_tool_name(raw, expected):
    assert sanitize_tool_name(raw) == expected


def _note_server():
    server = fastmcp.FastMCP("local")

    @server.tool
    def record_note(text: str) -> str:
        """Record a note."""
        return f"note:{text}"

    return server


def test_registry_exposes_namespaced_schemas(registry):
    registry.add_server(_note_server(), name="local")
    assert registry.tool_names()[:2] == ["bash", "submit"]
    assert "local__record_note" in registry.tool_names()


def test_registry_executes_function_tool(registry):
    registry.add_server(_note_server(), name="local")
    output = registry.execute({"tool": "local__record_note", "args": {"text": "hi"}})
    assert output["returncode"] == 0
    assert output["output"] == "note:hi"


def test_unknown_tool_is_returned_as_observation(registry):
    output = registry.execute({"tool": "does_not_exist", "args": {}})
    assert output["returncode"] == 1
    assert "Unknown tool" in output["output"]


def test_tool_exception_is_returned_as_observation(registry):
    server = fastmcp.FastMCP("broken")

    @server.tool
    def boom() -> str:
        """Always fails."""
        raise ValueError("kaboom")

    registry.add_server(server, name="broken")
    output = registry.execute({"tool": "broken__boom", "args": {}})
    assert output["returncode"] == -1
    assert "kaboom" in output["exception_info"]


def test_submit_raises_submitted(registry):
    with pytest.raises(Submitted):
        registry.execute({"tool": "submit", "args": {"summary": "all done"}})


def test_registry_native_tool(registry):
    def add(a: int, b: int) -> str:
        """Add two integers."""
        return str(a + b)

    registry.add_tool(add)
    assert "add" in registry.tool_names()
    output = registry.execute({"tool": "add", "args": {"a": 2, "b": 3}})
    assert output["returncode"] == 0
    assert output["output"] == "5"


def test_registry_native_tool_errors_are_observations(registry):
    def explode() -> str:
        """Always fails."""
        raise ValueError("kaboom")

    registry.add_tool(explode)
    output = registry.execute({"tool": "explode", "args": {}})
    assert output["returncode"] == -1
    assert "kaboom" in output["exception_info"]


def test_registry_can_disable_bash(runner):
    from minisweagent.bughunter.tools.registry import ToolRegistry
    from minisweagent.environments.local import LocalEnvironment

    registry = ToolRegistry(LocalEnvironment(), runner, include_bash=False)
    try:
        assert "bash" not in registry.tool_names()
        assert registry.execute({"tool": "bash", "command": "ls"})["returncode"] == 1
    finally:
        registry.close()


def test_registry_include_tools_filters_schemas(registry):
    server = fastmcp.FastMCP("many")

    @server.tool
    def alpha() -> str:
        """Alpha tool."""
        return "a"

    @server.tool
    def beta() -> str:
        """Beta tool."""
        return "b"

    registry.add_server(server, name="many", include_tools=["alpha"])
    assert "many__alpha" in registry.tool_names()
    assert "many__beta" not in registry.tool_names()
