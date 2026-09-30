from minisweagent.bughunter.agent import BughunterAgent
from minisweagent.bughunter.display import format_message
from minisweagent.bughunter.tools.registry import ToolRegistry
from minisweagent.environments.local import LocalEnvironment
from minisweagent.exceptions import Submitted
from minisweagent.models.test_models import DeterministicToolcallModel, make_toolcall_output


def test_format_message_renders_tool_name_and_json_args():
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"function": {"name": "update_password", "arguments": '{"password": "x"}'}}],
    }
    rendered = format_message(message)
    assert "update_password" in rendered
    assert '"password": "x"' in rendered


def test_format_message_renders_command_for_shell_tools():
    message = {"tool_calls": [{"function": {"name": "try_command", "arguments": '{"command": "ls -la"}'}}]}
    rendered = format_message(message)
    assert "try_command" in rendered
    assert "ls -la" in rendered


def test_format_message_falls_back_for_plain_messages():
    assert format_message({"role": "user", "content": "hello"}) == "hello"


def test_terminating_tool_gets_a_real_observation(runner):
    registry = ToolRegistry(LocalEnvironment(), runner)

    def finish(summary: str) -> str:
        """Finish the task."""
        raise Submitted(
            {"role": "exit", "content": summary, "extra": {"exit_status": "Submitted", "submission": summary}}
        )

    registry.add_tool(finish)
    model = DeterministicToolcallModel(
        outputs=[
            make_toolcall_output(
                None,
                [{"id": "c1"}],
                [{"tool": "finish", "args": {"summary": "done"}, "command": "finish", "tool_call_id": "c1"}],
            )
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
    try:
        result = agent.run("go")
        assert result["exit_status"] == "Submitted"
        tool_messages = [message for message in agent.messages if message.get("role") == "tool"]
        assert tool_messages and "done" in tool_messages[0]["content"]
        assert "was not executed" not in tool_messages[0]["content"]
    finally:
        registry.close()
