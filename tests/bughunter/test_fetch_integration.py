import json
import shutil

import pytest

from minisweagent.bughunter.agent import BughunterAgent
from minisweagent.bughunter.tools.mcp import AsyncRunner
from minisweagent.bughunter.tools.registry import ToolRegistry
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.test_models import DeterministicToolcallModel, make_toolcall_output

pytestmark = pytest.mark.slow


def _tool_call(name: str, arguments: dict, call_id: str):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


def test_fetch_mcp_round_trip():
    """Drive a real `uvx mcp-server-fetch` server through the agent with a scripted model."""
    if shutil.which("uvx") is None:
        pytest.skip("uvx is not available")

    runner = AsyncRunner()
    registry = ToolRegistry(LocalEnvironment(), runner)
    try:
        registry.add_server({"command": "uvx", "args": ["mcp-server-fetch"]}, name="fetch")
        assert "fetch__fetch" in registry.tool_names()

        model = DeterministicToolcallModel(
            outputs=[
                make_toolcall_output(
                    None,
                    [_tool_call("fetch__fetch", {"url": "https://example.com"}, "call_fetch")],
                    [
                        {
                            "tool": "fetch__fetch",
                            "args": {"url": "https://example.com"},
                            "command": "fetch__fetch",
                            "tool_call_id": "call_fetch",
                        }
                    ],
                ),
                make_toolcall_output(
                    None,
                    [],
                    [
                        {
                            "tool": "submit",
                            "args": {"summary": "done"},
                            "command": "submit",
                            "tool_call_id": "call_submit",
                        }
                    ],
                ),
            ]
        )
        agent = BughunterAgent(
            model,
            LocalEnvironment(),
            tools=registry,
            system_template="system",
            instance_template="{{task}}",
            mode="yolo",
            confirm_exit=False,
            cost_limit=0,
        )
        result = agent.run("fetch https://example.com")

        assert result["exit_status"] == "Submitted"
        tool_messages = [message for message in agent.messages if message.get("role") == "tool"]
        if not tool_messages or tool_messages[0]["extra"]["returncode"] != 0:
            pytest.skip("fetch server could not reach the network")
        assert "example.com" in tool_messages[0]["content"]
    finally:
        registry.close()
        runner.close()
