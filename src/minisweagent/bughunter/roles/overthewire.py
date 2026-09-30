"""OverTheWire Bandit role.

Solves https://overthewire.org/wargames/bandit/ level by level over SSH. The agent gets a
persistent :class:`BanditState` (current level, password, goal, memories, pins) plus native
tools to run remote commands, record findings and finish a level by submitting the next
password. ``OverTheWireRole.run`` connects, fetches the level goal, runs the agent, and
advances to the next level whenever a password is captured.
"""

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests

from minisweagent.bughunter.roles.base import AgentRole
from minisweagent.bughunter.tools.mcp import AsyncRunner
from minisweagent.bughunter.tools.registry import ToolRegistry
from minisweagent.bughunter.tools.ssh import SshManager
from minisweagent.exceptions import Submitted

logger = logging.getLogger("bughunter.overthewire")

BANDIT_HOST = "bandit.labs.overthewire.org"
BANDIT_PORT = 2220
MAX_PINS = 10
MAX_OUTPUT = 5000

SYSTEM_PROMPT = """\
You are a senior security expert with years of Linux and CTF experience. You are solving the
OverTheWire Bandit wargame, where each level requires locating a password on a remote server
and using it to log into the next level.

You cannot execute commands locally: use the `try_command` tool to run shell commands on the
remote host. We have already authenticated you as the current level's user, so assume every
command starts in a fresh /bin/bash shell in that user's home directory.
"""

GUIDANCE = """\
- Do not use placeholder values in any of your actions.
- Recall your memories to help you make decisions.
- Experiment frequently and adjust your approach to make progress.
- Be organized and methodical; every action should have a purpose.
- Assume each `try_command` starts a new /bin/bash shell in the user home directory.
- Add verbosity and failure information to your commands to assist with troubleshooting.
- Prefer full paths to files and directories.
- Be creative, but keep solutions simple.
- Avoid repeating commands that have already failed; read the output to understand why.
- Use /tmp for any file write operations.
- Passwords look like long base64 strings; watch for them.
- When you have the password for the next level, call `update_password(<password>)` to finish
  the level. Do not call it with a guess.
"""

TASK_TEMPLATE = """\
# Context

<current-level>
{level}
</current-level>

<current-level-details>
{details}
</current-level-details>

<memories>
{memories}
</memories>

<pinned>
{pinned}
</pinned>

# Goals

<previous-goals>
{previous_goals}
</previous-goals>

<current-goal>
{current_goal}
</current-goal>

# Guidance

{guidance}
"""


def get_bandit_level_description(level: int, *, timeout: float = 15.0) -> str:
    """Scrape a level goal from ``overthewire.org/wargames/bandit/bandit{level}.html``.

    Page ``banditN`` documents how to reach level N, so while at level L the goal for the
    next step is on page ``L + 1`` (e.g. at level 1 the goal to reach level 2 is bandit2.html).
    """
    try:
        response = requests.get(f"https://overthewire.org/wargames/bandit/bandit{level}.html", timeout=timeout)
        response.raise_for_status()
        goal = re.findall(r"Level Goal</h2>(.+)<h2", response.text, re.DOTALL)[0]
        goal = goal.replace("<p>", "").replace("</p>", "").strip()
        return re.sub("<.*?>", "", goal)
    except (requests.RequestException, IndexError) as e:
        return f"(could not fetch the goal for level {level}: {e})"


@dataclass
class BanditState:
    """Progress and scratch state, persisted between levels (optionally to ``state_file``)."""

    host: str = BANDIT_HOST
    port: int = BANDIT_PORT
    level: int = 0
    password: str = "bandit0"
    result: str = ""
    session: str = "bandit"
    level_details: str = ""
    goals: list[str] = field(default_factory=list)
    pins: list[str] = field(default_factory=list)
    memories: dict[str, str] = field(default_factory=dict)
    max_pins: int = MAX_PINS

    def solved(self) -> bool:
        return bool(self.result)

    def finish(self, password: str) -> None:
        self.result = password

    def advance(self) -> None:
        self.password = self.result
        self.result = ""
        self.level += 1

    def task(self) -> str:
        goals = self.goals or [f"Find the password for the next level ({self.level + 1})."]
        memories = "\n".join(f"- {key}: {value}" for key, value in self.memories.items()) or "No memories yet."
        return TASK_TEMPLATE.format(
            level=self.level,
            details=self.level_details,
            memories=memories,
            pinned="\n".join(self.pins) or "No pinned context yet.",
            previous_goals="\n".join(goals[:-1]) or "No previous goals.",
            current_goal=goals[-1],
            guidance=GUIDANCE,
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: Path) -> "BanditState":
        return cls(**json.loads(path.read_text()))


class OverTheWireRole(AgentRole):
    name = "overthewire"
    description = "Solve OverTheWire Bandit levels over SSH, advancing automatically."

    def __init__(self) -> None:
        self.state = BanditState()
        self._manager: SshManager | None = None
        self._runner: AsyncRunner | None = None
        self._state_file: Path | None = None
        self._timeout = 10.0

    def configure(self) -> dict:
        return {
            "agent": {
                "system_template": SYSTEM_PROMPT,
                "instance_template": "{{task}}",
                "mode": "yolo",
                "confirm_exit": False,
            },
            "tools": {
                "functions": False,
                "ssh": {"enabled": False},
                "mcp_servers": [],
                "include_bash": False,
                "binary_policy": {"mode": "blocklist"},
            },
        }

    def add_tools(self, registry: ToolRegistry, config: dict, runner: AsyncRunner) -> None:
        role = config.get("role", {})
        if state_file := role.get("state_file"):
            self._state_file = Path(state_file).expanduser()
            if self._state_file.is_file():
                self.state = BanditState.load(self._state_file)
        else:
            self.state.level = int(role.get("level", self.state.level))
            self.state.password = str(role.get("password", self.state.password))
        self._timeout = float(role.get("timeout", self._timeout))
        self._runner = runner
        self._manager = SshManager(known_hosts="none", connect_timeout=role.get("connect_timeout", 30.0))
        registry.add_cleanup(lambda: runner.run(self._manager.aclose_all()))
        for fn in self._tools():
            registry.add_tool(fn)

    def _tools(self) -> list:
        state = self.state

        async def try_command(command: str) -> str:
            """Execute a shell command on the remote OverTheWire host via SSH."""
            assert self._manager is not None
            try:
                output = await self._manager.exec(state.session, command, timeout=self._timeout)
            except Exception as e:
                return f"[ssh error] {type(e).__name__}: {e}"
            if len(output) > MAX_OUTPUT:
                output = output[:MAX_OUTPUT] + "\n[output truncated]"
            return output or "[command finished]"

        def update_goal(goal: str) -> str:
            """Add a new goal to the goal list."""
            state.goals.append(goal)
            return "Goal updated."

        def save_memory(key: str, content: str) -> str:
            """Save a memory (key/content) that persists across levels."""
            state.memories[key] = content
            return f"Stored '{key}'."

        def recall_memory(key: str) -> str:
            """Recall a previously stored memory by key."""
            return state.memories.get(key, "Not found.")

        def delete_memory(key: str) -> str:
            """Delete a stored memory by key."""
            state.memories.pop(key, None)
            return f"Forgot '{key}'."

        def pin_to_top(content: str) -> str:
            """Pin important content so it stays visible in the context."""
            state.pins.append(content)
            state.pins[:] = state.pins[-state.max_pins :]
            return "Pinned."

        def update_password(password: str) -> str:
            """Record the password for the next level and finish the current level."""
            state.finish(password)
            raise Submitted(
                {
                    "role": "exit",
                    "content": f"Solved level {state.level}. Password for level {state.level + 1}: {password}",
                    "extra": {"exit_status": "Submitted", "submission": password},
                }
            )

        return [try_command, update_goal, save_memory, recall_memory, delete_memory, pin_to_top, update_password]

    def run(self, agent, config: dict) -> dict:
        role = config.get("role", {})
        max_level = int(role.get("max_level", 0))
        result: dict = {}
        while max_level <= 0 or self.state.level <= max_level:
            self.state.level_details = get_bandit_level_description(self.state.level + 1)
            self.state.goals.append(
                f"Find the password for the next level ({self.state.level + 1}) and submit it with update_password."
            )
            if not self._connect():
                break
            try:
                result = agent.run(self.state.task())
            finally:
                self._disconnect()
            if not self.state.solved():
                logger.warning("Level %s not solved; stopping.", self.state.level)
                break
            logger.info("Solved level %s (next password captured).", self.state.level)
            self.state.advance()
            if self._state_file:
                self.state.save(self._state_file)
        return result

    def _connect(self) -> bool:
        assert self._manager is not None and self._runner is not None
        try:
            self._runner.run(
                self._manager.connect(
                    self.state.session,
                    host=self.state.host,
                    port=self.state.port,
                    username=f"bandit{self.state.level}",
                    password=self.state.password,
                )
            )
        except Exception as e:
            logger.error("Could not connect as bandit%s: %s", self.state.level, e)
            return False
        return True

    def _disconnect(self) -> None:
        assert self._manager is not None and self._runner is not None
        try:
            self._runner.run(self._manager.disconnect(self.state.session))
        except Exception as e:
            logger.warning("Disconnect failed: %s", e)
