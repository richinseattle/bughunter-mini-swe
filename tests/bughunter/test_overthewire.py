import pytest

from minisweagent.bughunter import roles
from minisweagent.bughunter.roles import get_role, role_names
from minisweagent.bughunter.roles.overthewire import (
    BanditState,
    OverTheWireRole,
    get_bandit_level_description,
)
from minisweagent.exceptions import Submitted


def test_role_registry():
    assert set(role_names()) == {"generic", "overthewire", "recon"}
    assert get_role("overthewire").name == "overthewire"
    assert get_role("generic").name == "generic"
    with pytest.raises(ValueError, match="Unknown role"):
        get_role("does-not-exist")


def test_overthewire_preset_disables_local_bash_and_ssh_tools():
    preset = OverTheWireRole().configure()
    assert preset["tools"]["include_bash"] is False
    assert preset["tools"]["ssh"]["enabled"] is False
    assert preset["agent"]["mode"] == "yolo"
    assert preset["agent"]["confirm_exit"] is False


def test_state_advance_and_task():
    state = BanditState(level=1, password="pw1", level_details="read the readme")
    state.goals.append("find the next password")
    state.finish("pw2")
    assert state.solved()
    state.advance()
    assert (state.level, state.password, state.result) == (2, "pw2", "")
    task = state.task()
    assert "read the readme" in task and "find the next password" in task


def test_state_save_and_load_roundtrip(tmp_path):
    state = BanditState(level=3, password="pw", memories={"k": "v"}, pins=["pin"], goals=["g"])
    state.save(tmp_path / "state.json")
    assert BanditState.load(tmp_path / "state.json") == state


def test_get_bandit_level_description_parses_goal(monkeypatch):
    class FakeResponse:
        text = "<h2>Level Goal</h2><p>The password is in a file.</p><h2>Commands</h2>"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(roles.overthewire.requests, "get", lambda *a, **k: FakeResponse())
    assert get_bandit_level_description(2) == "The password is in a file."


def test_get_bandit_level_description_handles_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise roles.overthewire.requests.RequestException("no network")

    monkeypatch.setattr(roles.overthewire.requests, "get", boom)
    assert "could not fetch" in get_bandit_level_description(2)


def test_role_tools_memories_goals_and_finish(registry, runner):
    role = OverTheWireRole()
    role.add_tools(registry, {"role": {}}, runner)

    assert {"try_command", "save_memory", "recall_memory", "update_password"} <= set(registry.tool_names())
    assert registry.execute({"tool": "save_memory", "args": {"key": "k", "content": "v"}})["returncode"] == 0
    assert registry.execute({"tool": "recall_memory", "args": {"key": "k"}})["output"] == "v"
    assert registry.execute({"tool": "delete_memory", "args": {"key": "k"}})["returncode"] == 0
    assert registry.execute({"tool": "recall_memory", "args": {"key": "k"}})["output"] == "Not found."
    assert registry.execute({"tool": "pin_to_top", "args": {"content": "p"}})["returncode"] == 0
    assert registry.execute({"tool": "update_goal", "args": {"goal": "g"}})["returncode"] == 0

    output = registry.execute({"tool": "try_command", "args": {"command": "ls"}})
    assert output["returncode"] == 0  # ssh failure is returned as text, not raised
    assert "[ssh error]" in output["output"]

    with pytest.raises(Submitted):
        registry.execute({"tool": "update_password", "args": {"password": "next-pw"}})
    assert role.state.result == "next-pw"


def test_run_loop_advances_levels(monkeypatch):
    role = OverTheWireRole()
    monkeypatch.setattr(roles.overthewire, "get_bandit_level_description", lambda level, **kw: f"goal {level}")
    monkeypatch.setattr(role, "_connect", lambda: True)
    monkeypatch.setattr(role, "_disconnect", lambda: None)

    class FakeAgent:
        def run(self, task):
            role.state.finish(f"pw{role.state.level + 1}")
            return {"exit_status": "Submitted", "submission": role.state.result}

    result = role.run(FakeAgent(), {"role": {"max_level": 2}})
    assert role.state.level == 3
    assert role.state.password == "pw3"
    assert result["submission"] == "pw3"
    assert len(role.state.goals) == 3


def test_run_loop_stops_when_level_not_solved(monkeypatch):
    role = OverTheWireRole()
    monkeypatch.setattr(roles.overthewire, "get_bandit_level_description", lambda level, **kw: f"goal {level}")
    monkeypatch.setattr(role, "_connect", lambda: True)
    monkeypatch.setattr(role, "_disconnect", lambda: None)

    class StuckAgent:
        def run(self, task):
            return {"exit_status": "LimitsExceeded", "submission": ""}

    role.run(StuckAgent(), {"role": {"max_level": 5}})
    assert role.state.level == 0


def test_run_stops_when_connection_fails(monkeypatch):
    role = OverTheWireRole()
    monkeypatch.setattr(roles.overthewire, "get_bandit_level_description", lambda level, **kw: f"goal {level}")
    monkeypatch.setattr(role, "_connect", lambda: False)

    class NeverCalled:
        def run(self, task):
            raise AssertionError("agent should not run without a connection")

    assert role.run(NeverCalled(), {"role": {}}) == {}


def test_run_fetches_next_level_page(monkeypatch):
    role = OverTheWireRole()
    role.state.level = 1
    seen: list[int] = []
    monkeypatch.setattr(
        roles.overthewire, "get_bandit_level_description", lambda level, **kw: seen.append(level) or "goal"
    )
    monkeypatch.setattr(role, "_connect", lambda: True)
    monkeypatch.setattr(role, "_disconnect", lambda: None)

    class StuckAgent:
        def run(self, task):
            return {}

    role.run(StuckAgent(), {"role": {"max_level": 1}})
    assert seen == [2]
