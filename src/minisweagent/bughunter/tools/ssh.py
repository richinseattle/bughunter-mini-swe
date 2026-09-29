"""SSH tools backed by asyncssh.

State (live connections) is kept in ``SshManager`` and keyed by a user-chosen session
name. Connections are created and used on the shared ``AsyncRunner`` event loop, so they
persist across tool calls. Credentials can be passed per call or supplied by a named host
profile in the config (recommended, so secrets stay out of the model's context).
"""

import asyncio
import os
from pathlib import Path
from typing import Any

import asyncssh
from fastmcp import FastMCP

DEFAULT_SESSION = "default"


class SshManager:
    def __init__(self, *, known_hosts: str | None = None, connect_timeout: float = 30.0, timeout: float = 30.0) -> None:
        self.known_hosts = known_hosts
        self.connect_timeout = connect_timeout
        self.timeout = timeout
        self._sessions: dict[str, asyncssh.SSHClientConnection] = {}

    async def connect(
        self,
        session: str,
        *,
        host: str,
        username: str | None = None,
        port: int = 22,
        password: str | None = None,
        private_key: str | None = None,
    ) -> str:
        if session in self._sessions:
            raise ValueError(f"Session '{session}' is already connected; disconnect it first.")
        options: dict[str, Any] = {"host": host, "port": port}
        if username:
            options["username"] = username
        if password:
            options["password"] = password
        if private_key:
            options["client_keys"] = [str(Path(private_key).expanduser())]
        if self.known_hosts == "none":
            options["known_hosts"] = None
        elif self.known_hosts:
            options["known_hosts"] = str(Path(self.known_hosts).expanduser())
        self._sessions[session] = await asyncio.wait_for(asyncssh.connect(**options), timeout=self.connect_timeout)
        return f"Connected session '{session}' to {username or '(default)'}@{host}:{port}"

    async def disconnect(self, session: str) -> str:
        connection = self._connection(session)
        connection.close()
        await connection.wait_closed()
        del self._sessions[session]
        return f"Disconnected session '{session}'"

    async def exec(self, session: str, command: str, *, timeout: float | None = None) -> str:
        connection = self._connection(session)
        result = await asyncio.wait_for(connection.run(command, check=False), timeout=timeout or self.timeout)
        output = result.stdout or ""
        if result.stderr:
            output = f"{output}\n[stderr]\n{result.stderr}" if output else f"[stderr]\n{result.stderr}"
        return f"exit_status={result.exit_status}\n{output.rstrip()}"

    async def upload(self, session: str, local_path: str, remote_path: str) -> str:
        source = Path(local_path).expanduser()
        async with self._connection(session).start_sftp_client() as sftp:
            await sftp.put(str(source), remote_path)
        return f"Uploaded {source} -> {remote_path} (session '{session}')"

    async def download(self, session: str, remote_path: str, local_path: str) -> str:
        destination = Path(local_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        async with self._connection(session).start_sftp_client() as sftp:
            await sftp.get(remote_path, str(destination))
        return f"Downloaded {remote_path} -> {destination} (session '{session}')"

    async def aclose_all(self) -> None:
        for connection in self._sessions.values():
            connection.close()
        for connection in list(self._sessions.values()):
            await connection.wait_closed()
        self._sessions.clear()

    def _connection(self, session: str) -> asyncssh.SSHClientConnection:
        try:
            return self._sessions[session]
        except KeyError:
            known = ", ".join(self._sessions) or "none"
            raise ValueError(f"Unknown session '{session}'. Connected sessions: {known}") from None


def build_ssh_server(manager: SshManager, profiles: dict[str, dict] | None = None) -> FastMCP:
    """Create an in-memory MCP server exposing the SSH tools (names are unprefixed)."""
    server = FastMCP("ssh")
    profiles = profiles or {}

    def resolve(session: str, host, username, port, password, private_key) -> dict[str, Any]:
        profile = profiles.get(session, {})
        return {
            "host": host or profile.get("host", ""),
            "username": username or profile.get("username"),
            "port": port if port is not None else profile.get("port", 22),
            "password": password or _env(profile.get("password_env")),
            "private_key": private_key or profile.get("private_key"),
        }

    @server.tool
    async def ssh_connect(
        session: str = DEFAULT_SESSION,
        host: str | None = None,
        username: str | None = None,
        port: int | None = None,
        password: str | None = None,
        private_key: str | None = None,
    ) -> str:
        """Open an SSH connection and remember it under `session` for later calls.

        If `session` matches a host profile from the config, that profile fills in any
        omitted host/username/port/credentials.
        """
        options = resolve(session, host, username, port, password, private_key)
        if not options["host"]:
            raise ValueError(f"No host given and no config profile named '{session}'.")
        return await manager.connect(session, **options)

    @server.tool
    async def ssh_disconnect(session: str = DEFAULT_SESSION) -> str:
        """Close a previously opened SSH session."""
        return await manager.disconnect(session)

    @server.tool
    async def ssh_exec(command: str, session: str = DEFAULT_SESSION, timeout: float | None = None) -> str:
        """Run a shell command over an open SSH session; returns exit status, stdout and stderr."""
        return await manager.exec(session, command, timeout=timeout)

    @server.tool
    async def ssh_upload(local_path: str, remote_path: str, session: str = DEFAULT_SESSION) -> str:
        """Upload a local file to the remote host over SFTP."""
        return await manager.upload(session, local_path, remote_path)

    @server.tool
    async def ssh_download(remote_path: str, local_path: str, session: str = DEFAULT_SESSION) -> str:
        """Download a remote file to the local filesystem over SFTP."""
        return await manager.download(session, remote_path, local_path)

    return server


def _env(name: str | None) -> str | None:
    return os.getenv(name) if name else None
