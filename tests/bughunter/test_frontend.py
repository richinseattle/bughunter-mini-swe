import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BUGHUNTER_DIR = REPO / "src" / "minisweagent" / "bughunter"


def _env() -> dict:
    return os.environ | {"MSWEA_CONFIGURED": "1", "MSWEA_SILENT_STARTUP": "1"}


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], cwd=cwd, env=_env(), capture_output=True, text=True, timeout=120)


def test_runs_from_its_own_directory():
    result = _run(["run.py", "--help"], BUGHUNTER_DIR)
    assert result.returncode == 0, result.stderr


def test_runs_as_module_from_repo_root():
    result = _run(["-m", "minisweagent.bughunter", "--help"], REPO)
    assert result.returncode == 0, result.stderr


def test_list_tools_exposes_local_and_ssh_tools():
    result = _run(["-m", "minisweagent.bughunter", "--list-tools"], REPO)
    assert result.returncode == 0, result.stderr
    assert "local__record_note" in result.stdout
    assert "ssh_exec" in result.stdout
