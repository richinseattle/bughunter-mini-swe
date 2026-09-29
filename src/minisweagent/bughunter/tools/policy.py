"""Best-effort allow/deny policy for local binaries executed through the bash tool.

This is a guardrail against casual and injected use of dangerous host binaries, not a
security boundary: interpreters (``python -c``, ``bash -c``), command substitution and
shell builtins can bypass static inspection. Use the Docker environment for real
isolation. Configure it from YAML under ``tools.binary_policy``.
"""

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODE = "blocklist"

# High-risk host binaries: remote access/reverse shells, bulk transfer/exfiltration,
# privilege escalation and destructive system operations.
DEFAULT_BLOCKLIST = frozenset(
    {
        "ssh",
        "scp",
        "sftp",
        "ssh-keygen",
        "ssh-add",
        "ssh-agent",
        "sshd",
        "nc",
        "ncat",
        "netcat",
        "socat",
        "telnet",
        "rsh",
        "rlogin",
        "rexec",
        "rcp",
        "curl",
        "wget",
        "ftp",
        "tftp",
        "rsync",
        "sudo",
        "doas",
        "su",
        "mkfs",
        "fdisk",
        "parted",
        "mount",
        "umount",
        "insmod",
        "modprobe",
        "iptables",
        "ip6tables",
        "nft",
        "systemctl",
        "reboot",
        "shutdown",
        "poweroff",
        "halt",
        "crontab",
    }
)

_SEPARATORS = {";", ";;", "&&", "||", "|", "|&", "&", "\n", "(", ")"}
_REDIRECTIONS = {">", ">>", "<", "<<", "<<<", "&>", "&>>", "<>", ">|"}
_WRAPPERS = {"sudo", "doas", "env", "command", "nohup", "time", "nice", "ionice", "setsid", "stdbuf", "exec"}
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def extract_executables(command: str) -> list[str]:
    """Best-effort list of binary names a shell command will execute.

    Splits on control operators with :mod:`shlex`, skips redirections, environment
    assignments and option flags, and takes the basename of command words. Wrappers such
    as ``sudo``/``env`` are reported as executables themselves.
    """
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        tokens = command.split()

    executables: list[str] = []
    expect = True
    for token in tokens:
        if token in _SEPARATORS:
            expect = True
        elif token in _REDIRECTIONS or token.startswith("-"):
            continue
        elif token in _WRAPPERS:
            executables.append(Path(token).name)
            expect = True
        elif _ASSIGNMENT.match(token):
            continue
        elif expect:
            executables.append(Path(token).name)
            expect = False
    return executables


@dataclass(frozen=True)
class BinaryPolicy:
    mode: str = "off"
    """One of "off", "blocklist" or "whitelist"."""
    blocklist: frozenset[str] = DEFAULT_BLOCKLIST
    whitelist: frozenset[str] = frozenset()

    @classmethod
    def from_config(cls, config: dict) -> "BinaryPolicy":
        mode = config.get("mode", DEFAULT_MODE)
        blocklist = config.get("blocklist")
        return cls(
            mode=mode,
            blocklist=frozenset(blocklist) if blocklist is not None else DEFAULT_BLOCKLIST,
            whitelist=frozenset(config.get("whitelist") or ()),
        )

    def check(self, command: str) -> str:
        """Return an error message if `command` is disallowed, otherwise an empty string."""
        if self.mode == "off":
            return ""
        executables = extract_executables(command)
        if self.mode == "blocklist":
            denied = sorted({name for name in executables if name in self.blocklist})
            if denied:
                return f"Blocked by binary policy: {', '.join(denied)}. Use the dedicated tools instead."
        elif self.mode == "whitelist":
            denied = sorted({name for name in executables if name not in self.whitelist})
            if denied:
                return f"Blocked by binary policy: {', '.join(denied)} (not in the whitelist)."
        return ""
