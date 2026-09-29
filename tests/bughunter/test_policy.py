import pytest

from minisweagent.bughunter.tools.policy import BinaryPolicy, extract_executables
from minisweagent.bughunter.tools.registry import ToolRegistry
from minisweagent.environments.local import LocalEnvironment


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("ls -la", ["ls"]),
        ("cat a.txt | grep x && rg y", ["cat", "grep", "rg"]),
        ("FOO=bar python -c 'print(1)'", ["python"]),
        ("/usr/bin/ssh host", ["ssh"]),
        ("echo a; wget http://example.com", ["echo", "wget"]),
        ("sudo apt-get update", ["sudo", "apt-get"]),
        ("", []),
    ],
)
def test_extract_executables(command, expected):
    assert extract_executables(command) == expected


def test_default_blocklist_blocks_remote_binaries():
    policy = BinaryPolicy.from_config({})
    assert "Blocked" in policy.check("ssh host")
    assert policy.check("ls -la") == ""


def test_whitelist_blocks_everything_else():
    policy = BinaryPolicy.from_config({"mode": "whitelist", "whitelist": ["ls", "cat"]})
    assert policy.check("cat file") == ""
    assert "Blocked" in policy.check("curl http://example.com")


def test_off_mode_allows_everything():
    assert BinaryPolicy.from_config({"mode": "off"}).check("ssh host") == ""


def test_custom_blocklist_replaces_default():
    policy = BinaryPolicy.from_config({"mode": "blocklist", "blocklist": ["foo"]})
    assert "Blocked" in policy.check("foo bar")
    assert policy.check("ssh host") == ""


def test_registry_blocks_bash_action(runner):
    registry = ToolRegistry(LocalEnvironment(), runner, binary_policy=BinaryPolicy.from_config({}))
    try:
        output = registry.execute({"tool": "bash", "command": "ssh example.com"})
        assert output["returncode"] == 1
        assert "Blocked" in output["output"]
    finally:
        registry.close()


def test_registry_allows_safe_bash_action(runner):
    registry = ToolRegistry(LocalEnvironment(), runner, binary_policy=BinaryPolicy.from_config({}))
    try:
        output = registry.execute({"tool": "bash", "command": "echo hi"})
        assert output["returncode"] == 0
        assert "hi" in output["output"]
    finally:
        registry.close()
