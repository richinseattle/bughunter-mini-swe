# bughunter

`mini-swe-agent` extended with local function tools, SSH tools (asyncssh) and MCP servers.
Run it three ways: the `bughunter` console script, `python -m minisweagent.bughunter`, or
`python run.py` from this directory.

## Configuration files

| File | Purpose |
| --- | --- |
| `config/bughunter.yaml` | Packaged defaults: prompts, observation templates, binary policy, SSH, MCP catalog/groups. |
| `$MSWEA_GLOBAL_CONFIG_DIR/bughunter.yaml` | User/global overrides. Define MCP servers here and enable them by group. Defaults to `~/.config/mini-swe-agent/bughunter.yaml`. |
| `./bughunter.yaml` | Project-local overrides; takes precedence over the global file. |
| `$MSWEA_GLOBAL_CONFIG_DIR/llm.yaml` and `./llm.yaml` | LLM service endpoints and keys. Override with `-L/--llm-config`. |
| `./.env` | Secrets loaded by default (project wins over global). |

Layering (later wins): packaged defaults → global `bughunter.yaml` → local `./bughunter.yaml`
→ `-c` specs → CLI flags. Values may reference environment variables with `${VAR}`.

## MCP groups

Define servers once, group them, then enable only what you need so the model's tool list
stays small:

```yaml
# ~/.config/mini-swe-agent/bughunter.yaml
tools:
  mcp_server_catalog:
    fetch: {command: uvx, args: [mcp-server-fetch]}
    shodan:
      url: https://mcp.shodan.io/mcp
      headers: {Authorization: "Bearer ${SHODAN_API_KEY}"}
  mcp_server_groups:
    web: [fetch]
    recon: [fetch, shodan]
  mcp_servers: [web]
```

Enable groups per run with `--mcp-group recon` (repeatable), `--mcp-server fetch`, or by
setting `tools.mcp_servers`. See `config/bughunter.global.example.yaml`.

## LLM services

```yaml
# ~/.config/mini-swe-agent/llm.yaml
default_service: deepseek
services:
  deepseek:
    model: deepseek/deepseek-flash
    api_base: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY
```

Select with `--service deepseek`, `BUGHUNTER_SERVICE`, or `default_service`. Keys are never
written to the trajectory. See `config/llm.example.yaml`.

## Binary policy

`tools.binary_policy` is a best-effort guardrail on bash execution, not a sandbox:

```yaml
tools:
  binary_policy:
    mode: blocklist        # blocklist | whitelist | off
    # blocklist: [ssh, nc, curl, sudo, ...]   # replaces the default set
    # whitelist: [ls, cat, grep, git, python]
```

Interpreters and command substitution can bypass static inspection — use the docker
environment for real isolation.
