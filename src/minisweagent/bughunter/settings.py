"""Loading and resolving bughunter settings.

Responsibilities:

- Load ``.env`` files by default (project, then configured paths, then the global config).
- Expand ``${VAR}``/``$VAR`` references in config values from the environment.
- Load the separate LLM service file(s) and resolve a named service to a model config.
- Expand MCP server groups into concrete server specs.
"""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from minisweagent import global_config_dir
from minisweagent.utils.serialize import recursive_merge

GLOBAL_CONFIG_FILE = global_config_dir / "bughunter.yaml"
GLOBAL_LLM_FILE = global_config_dir / "llm.yaml"
PROJECT_LLM_FILE = Path.cwd() / "llm.yaml"
LOCAL_CONFIG_NAME = "bughunter.yaml"


def local_config_file() -> Path:
    """Project-local config that overrides the global one (``./bughunter.yaml``)."""
    return Path.cwd() / LOCAL_CONFIG_NAME


def load_env_files(*paths: Path) -> None:
    """Load ``.env`` files without overriding variables already set in the environment.

    Project ``.env`` wins over the global one; real environment variables win over both.
    """
    for path in (Path.cwd() / ".env", *paths, global_config_dir / ".env"):
        if path.is_file():
            load_dotenv(path, override=False)


def expand_env(value):
    """Recursively expand ``${VAR}``/``$VAR`` in strings, leaving unknown variables intact."""
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, dict):
        return {key: expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_env(item) for item in value]
    return value


def load_yaml(path: Path | str) -> dict:
    path = Path(path).expanduser()
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.is_file() else {}


def load_bughunter_config(config_specs: list[str], overrides: dict, default_config_file: Path) -> dict:
    """Layer the bughunter config: packaged defaults < global < local (``./bughunter.yaml``) < ``-c`` specs < CLI flags.

    If ``-c`` replaces the packaged default file, that key is simply absent from the specs
    and the packaged defaults are not loaded (matching mini-swe-agent's documented behavior).
    """
    from minisweagent.config import get_config_from_spec

    parts: list[dict] = []
    specs = [str(spec) for spec in config_specs]
    uses_default = str(default_config_file) in specs
    if uses_default:
        parts.append(load_yaml(default_config_file))
        specs = [spec for spec in specs if spec != str(default_config_file)]
    parts.append(load_yaml(GLOBAL_CONFIG_FILE))
    parts.append(load_yaml(local_config_file()))
    parts += [get_config_from_spec(spec) for spec in specs]
    parts.append(overrides)

    config = recursive_merge(*parts)
    config["tools"] = expand_env(config.get("tools", {}))
    config["environment"] = expand_env(config.get("environment", {}))
    return config


def load_llm_config(paths: list[Path] | None = None) -> dict:
    """Merge LLM service files. Precedence: global < ``./llm.yaml`` < explicit paths."""
    configs = [load_yaml(GLOBAL_LLM_FILE), load_yaml(PROJECT_LLM_FILE)]
    configs += [load_yaml(path) for path in (paths or [])]
    return expand_env(recursive_merge(*configs))


def pick_service(llm_config: dict, requested: str | None = None) -> str | None:
    return requested or os.getenv("BUGHUNTER_SERVICE") or llm_config.get("default_service")


def resolve_service(llm_config: dict, service_name: str) -> tuple[dict, str | None]:
    """Resolve a named service to ``(model_config, api_key)``.

    The API key is returned separately so it is never persisted in the trajectory; it can
    be given inline as ``api_key`` or via ``api_key_env`` (resolved from env/``.env``).
    """
    services = llm_config.get("services", {})
    if service_name not in services:
        raise ValueError(f"Unknown LLM service '{service_name}'. Available: {sorted(services) or 'none'}")
    service = services[service_name]
    if not service.get("model"):
        raise ValueError(f"LLM service '{service_name}' is missing 'model'.")
    model_kwargs = dict(service.get("model_kwargs", {}))
    if api_base := service.get("api_base"):
        model_kwargs["api_base"] = api_base
    api_key = service.get("api_key") or (os.getenv(service["api_key_env"]) if service.get("api_key_env") else None)
    return {"model_name": service["model"], "model_kwargs": model_kwargs}, api_key


def resolve_mcp_servers(tools_config: dict) -> list[dict]:
    """Expand the enabled MCP groups/servers into concrete server specs.

    ``tools.mcp_server_catalog`` defines servers, ``tools.mcp_server_groups`` maps group
    names to server names, and ``tools.mcp_servers`` lists what to enable (group names,
    catalog names, or inline specs). Only enabled servers are started, keeping the
    advertised tool list small.
    """
    catalog: dict = tools_config.get("mcp_server_catalog", {}) or {}
    groups: dict = tools_config.get("mcp_server_groups", {}) or {}
    resolved: list[dict] = []
    seen: set[str] = set()

    def add(name: str, spec: dict) -> None:
        if name in seen or spec.get("enabled", True) is False:
            return
        seen.add(name)
        resolved.append({"name": name, **spec})

    for entry in tools_config.get("mcp_servers", []) or []:
        if isinstance(entry, dict):
            add(entry["name"], entry)
        elif entry in groups:
            for name in groups[entry]:
                if name not in catalog:
                    raise ValueError(f"MCP group '{entry}' references unknown server '{name}'.")
                add(name, catalog[name])
        elif entry in catalog:
            add(entry, catalog[entry])
        else:
            raise ValueError(f"Unknown MCP server or group '{entry}'.")
    return resolved
