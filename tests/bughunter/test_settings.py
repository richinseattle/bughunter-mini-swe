import os

import pytest

from minisweagent.bughunter import settings


def test_expand_env_replaces_known_and_keeps_unknown(monkeypatch):
    monkeypatch.setenv("BUGHUNTER_TEST_TOKEN", "secret")
    expanded = settings.expand_env({"a": "${BUGHUNTER_TEST_TOKEN}", "b": ["${DOES_NOT_EXIST}"], "c": 3})
    assert expanded == {"a": "secret", "b": ["${DOES_NOT_EXIST}"], "c": 3}


def test_load_env_files_does_not_override_existing(tmp_path, monkeypatch):
    monkeypatch.delenv("BUGHUNTER_TEST_DOTENV", raising=False)
    (tmp_path / ".env").write_text("BUGHUNTER_TEST_DOTENV=from_dotenv\n")
    monkeypatch.chdir(tmp_path)
    settings.load_env_files()
    assert os.environ["BUGHUNTER_TEST_DOTENV"] == "from_dotenv"


def test_resolve_service_inline_key():
    llm = {"services": {"openai": {"model": "openai/gpt-5.4", "api_key": "sk-xyz", "api_base": "https://api"}}}
    model_config, api_key = settings.resolve_service(llm, "openai")
    assert model_config == {"model_name": "openai/gpt-5.4", "model_kwargs": {"api_base": "https://api"}}
    assert api_key == "sk-xyz"


def test_resolve_service_reads_api_key_env(monkeypatch):
    monkeypatch.setenv("BUGHUNTER_TEST_KEY", "env-key")
    llm = {"services": {"svc": {"model": "openai/x", "api_key_env": "BUGHUNTER_TEST_KEY"}}}
    assert settings.resolve_service(llm, "svc")[1] == "env-key"


def test_resolve_service_unknown_raises():
    with pytest.raises(ValueError, match="Unknown LLM service"):
        settings.resolve_service({"services": {}}, "nope")


def test_load_llm_config_precedence(tmp_path, monkeypatch):
    global_file, project_file, explicit_file = (tmp_path / name for name in ("g.yaml", "p.yaml", "e.yaml"))
    global_file.write_text("services:\n  svc: {model: global}\n")
    project_file.write_text("services:\n  svc: {model: project}\n")
    explicit_file.write_text("services:\n  svc: {model: explicit}\n")
    monkeypatch.setattr(settings, "GLOBAL_LLM_FILE", global_file)
    monkeypatch.setattr(settings, "PROJECT_LLM_FILE", project_file)
    assert settings.load_llm_config([explicit_file])["services"]["svc"]["model"] == "explicit"
    assert settings.load_llm_config()["services"]["svc"]["model"] == "project"


def test_resolve_mcp_servers_expands_groups():
    tools = {
        "mcp_server_catalog": {"fetch": {"command": "uvx"}, "shodan": {"url": "https://x"}},
        "mcp_server_groups": {"recon": ["fetch", "shodan"]},
        "mcp_servers": ["recon"],
    }
    servers = settings.resolve_mcp_servers(tools)
    assert [server["name"] for server in servers] == ["fetch", "shodan"]
    assert servers[1]["url"] == "https://x"


def test_resolve_mcp_servers_accepts_inline_and_skips_disabled():
    tools = {"mcp_servers": [{"name": "a", "command": "uvx"}, {"name": "b", "command": "uvx", "enabled": False}]}
    assert [server["name"] for server in settings.resolve_mcp_servers(tools)] == ["a"]


def test_resolve_mcp_servers_unknown_raises():
    with pytest.raises(ValueError, match="Unknown MCP server or group"):
        settings.resolve_mcp_servers({"mcp_servers": ["missing"]})


def test_local_config_overrides_global_and_packaged_defaults(tmp_path, monkeypatch):
    default = tmp_path / "default.yaml"
    default.write_text("agent: {cost_limit: 3.0, mode: confirm}\ntools: {ssh: {enabled: true}}\n")
    global_file = tmp_path / "global.yaml"
    global_file.write_text("agent: {cost_limit: 5.0}\ntools: {ssh: {enabled: false}, mcp_servers: [web]}\n")
    local = tmp_path / "local.yaml"
    local.write_text("agent: {cost_limit: 7.0}\ntools: {mcp_servers: [recon]}\n")
    monkeypatch.setattr(settings, "GLOBAL_CONFIG_FILE", global_file)
    monkeypatch.setattr(settings, "local_config_file", lambda: local)

    config = settings.load_bughunter_config([str(default)], {}, default)

    assert config["agent"] == {"cost_limit": 7.0, "mode": "confirm"}
    assert config["tools"] == {"ssh": {"enabled": False}, "mcp_servers": ["recon"]}


def test_explicit_c_spec_overrides_local(tmp_path, monkeypatch):
    default = tmp_path / "default.yaml"
    default.write_text("agent: {cost_limit: 3.0}\n")
    local = tmp_path / "local.yaml"
    local.write_text("agent: {cost_limit: 7.0}\n")
    extra = tmp_path / "extra.yaml"
    extra.write_text("agent: {cost_limit: 9.0}\n")
    monkeypatch.setattr(settings, "GLOBAL_CONFIG_FILE", tmp_path / "missing-global.yaml")
    monkeypatch.setattr(settings, "local_config_file", lambda: local)

    config = settings.load_bughunter_config([str(default), str(extra)], {}, default)

    assert config["agent"]["cost_limit"] == 9.0


def test_replaced_default_is_not_loaded_but_local_still_applies(tmp_path, monkeypatch):
    default = tmp_path / "default.yaml"
    default.write_text("agent: {cost_limit: 3.0}\n")
    local = tmp_path / "local.yaml"
    local.write_text("agent: {mode: confirm}\n")
    monkeypatch.setattr(settings, "GLOBAL_CONFIG_FILE", tmp_path / "missing-global.yaml")
    monkeypatch.setattr(settings, "local_config_file", lambda: local)

    config = settings.load_bughunter_config(["agent.cost_limit=1.0"], {}, default)

    assert config["agent"] == {"cost_limit": 1.0, "mode": "confirm"}
