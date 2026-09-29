import pytest

from minisweagent.bughunter.tools.mcp import AsyncRunner
from minisweagent.bughunter.tools.registry import ToolRegistry
from minisweagent.environments.local import LocalEnvironment


@pytest.fixture
def runner():
    runner = AsyncRunner()
    yield runner
    runner.close()


@pytest.fixture
def registry(runner):
    registry = ToolRegistry(LocalEnvironment(), runner)
    yield registry
    registry.close()
