import asyncssh
import pytest

from minisweagent.bughunter.tools.registry import ToolRegistry
from minisweagent.bughunter.tools.ssh import SshManager, build_ssh_server
from minisweagent.environments.local import LocalEnvironment

pytest.importorskip("fastmcp")

SSH_TOOLS = ("ssh_connect", "ssh_disconnect", "ssh_exec", "ssh_upload", "ssh_download")


def _add_ssh(registry, manager: SshManager | None = None):
    registry.add_server(build_ssh_server(manager or SshManager(known_hosts="none")), name="ssh", prefix="")


def test_ssh_tools_are_exposed_with_exact_names(registry):
    _add_ssh(registry)
    assert all(name in registry.tool_names() for name in SSH_TOOLS)


def test_ssh_exec_unknown_session_is_observation(registry):
    _add_ssh(registry)
    output = registry.execute({"tool": "ssh_exec", "args": {"command": "ls"}})
    assert output["returncode"] == -1
    assert "Unknown session" in output["exception_info"]


def test_ssh_connect_without_host_or_profile_is_observation(registry):
    _add_ssh(registry)
    output = registry.execute({"tool": "ssh_connect", "args": {"session": "target"}})
    assert output["returncode"] == -1
    assert "no config profile" in output["exception_info"]


class _AcceptAllServer(asyncssh.SSHServer):
    def begin_auth(self, username: str) -> bool:
        return False


async def _handle_client(process: asyncssh.SSHServerProcess) -> None:
    command = process.command or ""
    if command.startswith("echo "):
        process.stdout.write(command.removeprefix("echo ") + "\n")
    else:
        process.stderr.write(f"unsupported: {command}\n")
        process.exit(127)
        return
    process.exit(0)


async def _start_server(tmp_path):
    return await asyncssh.create_server(
        _AcceptAllServer,
        "127.0.0.1",
        0,
        server_host_keys=[asyncssh.generate_private_key("ssh-ed25519")],
        process_factory=_handle_client,
        sftp_factory=lambda channel: asyncssh.SFTPServer(channel, chroot=str(tmp_path).encode()),
    )


@pytest.mark.slow
def test_ssh_end_to_end_against_local_server(runner, tmp_path):
    acceptor = runner.run(_start_server(tmp_path))
    manager = SshManager(known_hosts="none")
    registry = ToolRegistry(LocalEnvironment(), runner)
    registry.add_server(build_ssh_server(manager), name="ssh", prefix="")
    registry.add_cleanup(lambda: runner.run(manager.aclose_all()))
    try:
        connect = registry.execute(
            {
                "tool": "ssh_connect",
                "args": {"session": "t", "host": "127.0.0.1", "port": acceptor.get_port(), "username": "tester"},
            }
        )
        assert connect["returncode"] == 0, connect
        executed = registry.execute({"tool": "ssh_exec", "args": {"session": "t", "command": "echo hello"}})
        assert executed["returncode"] == 0
        assert "hello" in executed["output"]

        local = tmp_path / "local.txt"
        local.write_text("payload")
        uploaded = registry.execute(
            {
                "tool": "ssh_upload",
                "args": {"session": "t", "local_path": str(local), "remote_path": "/remote.txt"},
            }
        )
        assert uploaded["returncode"] == 0, uploaded

        destination = tmp_path / "downloaded.txt"
        downloaded = registry.execute(
            {
                "tool": "ssh_download",
                "args": {"session": "t", "remote_path": "/remote.txt", "local_path": str(destination)},
            }
        )
        assert downloaded["returncode"] == 0, downloaded
        assert destination.read_text() == "payload"

        assert registry.execute({"tool": "ssh_disconnect", "args": {"session": "t"}})["returncode"] == 0
    finally:
        registry.close()
        acceptor.close()
        runner.run(acceptor.wait_closed())
