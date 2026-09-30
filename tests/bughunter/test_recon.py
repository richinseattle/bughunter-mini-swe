from minisweagent.bughunter.roles import get_role
from minisweagent.bughunter.roles.recon import ReconRole


def test_recon_preset_enables_recon_group_and_finding_tools_only():
    preset = ReconRole().configure()
    assert preset["tools"]["mcp_servers"] == ["recon"]
    assert preset["tools"]["functions"] is False
    assert get_role("recon").name == "recon"


def test_recon_tools_record_and_report_findings(registry, runner):
    role = ReconRole()
    role.add_tools(registry, {"role": {"target": "example.com"}}, runner)
    assert role.state.target == "example.com"

    assert (
        registry.execute({"tool": "save_finding", "args": {"key": "open_port", "detail": "22/tcp"}})["returncode"] == 0
    )
    assert "22/tcp" in registry.execute({"tool": "list_findings", "args": {}})["output"]
    assert registry.execute({"tool": "recall_finding", "args": {"key": "open_port"}})["output"] == "22/tcp"
    assert registry.execute({"tool": "delete_finding", "args": {"key": "open_port"}})["returncode"] == 0
    assert registry.execute({"tool": "recall_finding", "args": {"key": "open_port"}})["output"] == "Not found."
    assert registry.execute({"tool": "set_target", "args": {"target": "acme.test"}})["returncode"] == 0
    assert role.state.target == "acme.test"


def test_recon_run_prefers_task_and_falls_back_to_target():
    role = ReconRole()
    seen = {}

    class FakeAgent:
        def run(self, task):
            seen["task"] = task
            return {"exit_status": "Submitted", "submission": ""}

    role.state.target = "acme.test"
    role.run(FakeAgent(), {})
    assert "acme.test" in seen["task"]
    role.run(FakeAgent(), {"run": {"task": "scan 10.0.0.0/24"}})
    assert seen["task"] == "scan 10.0.0.0/24"


def test_build_registry_skips_a_broken_mcp_server(runner):
    from minisweagent.bughunter.run import _build_registry
    from minisweagent.environments.local import LocalEnvironment

    config = {
        "tools": {
            "functions": False,
            "ssh": {"enabled": False},
            "mcp_servers": [{"name": "broken", "command": "definitely-not-a-real-binary-xyz", "args": []}],
        }
    }
    registry = _build_registry(config, LocalEnvironment(), runner)
    try:
        assert "broken" not in registry.tool_names()
    finally:
        registry.close()
