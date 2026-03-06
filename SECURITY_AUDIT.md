# Security Audit Report - HolmesGPT

**Date**: 2026-03-06
**Scope**: Full codebase security review

---

## Critical Findings

### 1. No Authentication on API Server Endpoints
**Severity**: CRITICAL
**Location**: `server.py` (all endpoints)

The FastAPI server exposes all endpoints without any authentication:
- `POST /api/investigate` - triggers LLM investigations
- `POST /api/stream/investigate` - streaming investigations
- `POST /api/issue_chat` - issue conversations
- `POST /api/chat` - general chat with tool execution
- `POST /api/checks/execute` - health check execution
- `GET /api/model` - exposes model configuration

Any network-reachable entity can trigger LLM calls (consuming API credits), access cluster data via tools, and execute bash commands within the allowed list. Combined with the `0.0.0.0` default binding (`holmes/common/env_vars.py:29`), this creates significant exposure.

**Recommendation**: Add authentication middleware (API key, JWT, or mTLS). At minimum, restrict binding to `127.0.0.1` for local-only deployments.

### 2. Jinja2 Template Injection via LLM-Controlled Tool Parameters
**Severity**: CRITICAL
**Location**: `holmes/core/tools.py:535-547`

YAML-defined toolset commands use Jinja2 `Template()` to render commands with LLM-supplied parameters:

```python
def __invoke_command(self, params):
    command = os.path.expandvars(self.command)
    template = Template(command)
    rendered_command = template.render(context)
    # rendered_command is then executed via shell=True
```

While parameters are sanitized with `shlex.quote()` (`tools.py:135-146`), Jinja2 templates can potentially be exploited if the LLM is manipulated to inject Jinja2 syntax. The `sanitize()` function only shell-quotes values, it does not escape Jinja2 metacharacters (`{{`, `{%`). If an attacker can influence the LLM's parameter choices, they could inject Jinja2 template directives.

**Recommendation**: Use `jinja2.sandbox.SandboxedEnvironment` instead of bare `Template()`. Validate that parameter values don't contain Jinja2 syntax.

### 3. Wildcard CORS with Credentials in Experimental Server
**Severity**: HIGH
**Location**: `experimental/ag-ui/server-agui.py:81-87`

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

This allows any website to make authenticated cross-origin requests. While in the `experimental/` directory, this file is included in the production Docker image (`Dockerfile` line 151). Combined with zero authentication, any web page can trigger API calls.

**Recommendation**: Restrict `allow_origins` to specific trusted domains. Never combine `allow_origins=["*"]` with `allow_credentials=True`.

---

## High Severity Findings

### 4. `BASH_TOOL_UNSAFE_ALLOW_ALL` Environment Variable
**Severity**: HIGH
**Location**: `holmes/common/env_vars.py:90`

```python
BASH_TOOL_UNSAFE_ALLOW_ALL = load_bool("BASH_TOOL_UNSAFE_ALLOW_ALL", False)
```

Setting this env var to `true` disables all bash command validation, allowing the LLM to execute arbitrary commands (except `sudo`/`su`). While default is `False`, accidental or malicious enablement bypasses all safety controls.

**Recommendation**: Add a startup warning when this is enabled. Consider requiring a more explicit opt-in (e.g., a config file change, not just an env var). Log every command executed when this mode is active.

### 5. Shell Command Execution via `shell=True` with LLM-Controlled Input
**Severity**: HIGH
**Location**: `holmes/core/tools.py:562-574`, `holmes/plugins/toolsets/bash/common/bash.py:29-36`

All toolset command execution uses `subprocess.run(..., shell=True)` or `subprocess.Popen(..., shell=True)`. While parameters are `shlex.quote()`-ed, the rendered command (after Jinja2 templating) is passed as a single shell string. This is a defense-in-depth concern - if the sanitization is ever bypassed, full shell injection is possible.

The `sanitize()` function at `tools.py:135-142` provides the main defense:
```python
def sanitize(param):
    if param == "":
        return ""
    return shlex.quote(str(param))
```

Note: Empty strings bypass quoting. While intentional (per the comment), this could be exploited if an LLM is manipulated to provide empty strings for parameters that affect command structure.

**Recommendation**: Consider using `shell=False` with explicit argument lists where possible. Add defense-in-depth checks on the fully rendered command.

### 6. `os.path.expandvars()` on Commands Before Execution
**Severity**: HIGH
**Location**: `holmes/core/tools.py:537, 545`

```python
command = os.path.expandvars(self.command)
```

This expands `$VAR` and `${VAR}` in command templates before Jinja2 rendering. If the command template contains references to environment variables, those are expanded with the server process's full environment - which may contain API keys, database credentials, etc. An LLM could potentially craft parameters that leverage this to extract secrets.

**Recommendation**: Restrict `expandvars()` to a controlled set of environment variables, or remove it entirely and use Jinja2's `{{ env.VAR }}` syntax with an explicit allowlist.

### 7. Docker Container Runs as Root
**Severity**: HIGH
**Location**: `Dockerfile`

The main Dockerfile has no `USER` directive, meaning the container runs as root. This increases the blast radius if the container is compromised. The operator Dockerfile (`Dockerfile.operator`) correctly uses a non-root user (UID 10001).

**Recommendation**: Add a non-root user to the main Dockerfile, similar to the operator pattern.

---

## Medium Severity Findings

### 8. Missing HTTP Request Timeouts
**Severity**: MEDIUM
**Locations**: Multiple source plugins lack timeout parameters on `requests.get()`/`requests.post()` calls:

| File | Line |
|------|------|
| `holmes/plugins/sources/prometheus/plugin.py` | 78 |
| `holmes/plugins/sources/github/__init__.py` | 35, 69 |
| `holmes/plugins/sources/opsgenie/__init__.py` | 34, 73, 85 |
| `holmes/plugins/sources/jira/__init__.py` | 22, 91, 113 |
| `holmes/plugins/sources/pagerduty/__init__.py` | 37, 67, 124 |
| `holmes/plugins/toolsets/grafana/loki_api.py` | 50 |

Without timeouts, a slow or unresponsive external service can hang the server indefinitely, causing denial of service.

**Recommendation**: Add explicit `timeout=30` (or configurable) to all `requests` calls.

### 9. Grafana-Proxied Prometheus Defaults to `verify_ssl=False`
**Severity**: MEDIUM
**Location**: `holmes/plugins/toolsets/prometheus/prometheus.py:199`

```python
class GrafanaProxiedPrometheusConfig:
    verify_ssl: bool = False
```

When Prometheus is accessed through a Grafana proxy, SSL verification is disabled by default, enabling MITM attacks on this traffic.

**Recommendation**: Default to `verify_ssl=True` and document how to disable it if needed.

### 10. Hardcoded Bash Block List is Minimal
**Severity**: MEDIUM
**Location**: `holmes/plugins/toolsets/bash/common/config.py:9-12`

```python
HARDCODED_BLOCKS: List[str] = [
    "sudo",
    "su",
]
```

Only `sudo` and `su` are permanently blocked. Dangerous commands like `rm`, `dd`, `mkfs`, `chmod`, `chown`, `reboot`, `shutdown`, `kill`, `pkill`, `iptables`, `curl | bash`, `wget | bash` are not in the hardcoded block list. They would need to be in the deny list or require user approval, but the default deny list is empty (`DEFAULT_DENY_LIST: List[str] = []`).

**Recommendation**: Expand the hardcoded blocks to include destructive commands (`rm -rf`, `dd`, `mkfs`, etc.) and expand the default deny list.

### 11. Sensitive Data Exposure in Error Responses
**Severity**: MEDIUM
**Location**: `server.py:292-294, 372-373, 531-533`

```python
except Exception as e:
    raise HTTPException(status_code=500, detail=str(e))
```

Internal exception details are returned directly to the client. This can leak internal paths, database connection strings, or other sensitive implementation details.

**Recommendation**: Return generic error messages to clients. Log detailed errors server-side only.

### 12. Header Passthrough to MCP Servers
**Severity**: MEDIUM
**Location**: `server.py:386-411`

The `extract_passthrough_headers()` function forwards most request headers to MCP servers. While `authorization`, `cookie`, and `set-cookie` are blocked by default, other potentially sensitive headers (e.g., `X-API-Key`, `X-Auth-Token`, custom auth headers) are forwarded.

**Recommendation**: Use an allowlist approach instead of a blocklist for header passthrough.

### 13. Interactive Mode Executes User Commands via Shell
**Severity**: MEDIUM
**Location**: `holmes/interactive.py:813-814`

```python
result = subprocess.run(bash_command, shell=True, capture_output=True, text=True)
```

The interactive CLI mode allows users to run arbitrary bash commands. While this is expected for a CLI tool, the commands are executed with `shell=True` and no validation.

**Recommendation**: This is acceptable for a CLI tool (the user already has shell access), but should be documented as intentional behavior.

---

## Low Severity Findings

### 14. Binary Permissions Set to 777
**Location**: `Dockerfile:43, 58`

```dockerfile
chmod 777 kube-lineage
chmod 777 argocd
```

Binaries should use `755` (owner execute, world read+execute) not `777` (world writable).

### 15. Hardcoded Sentry DSN in Environment
**Location**: `holmes/common/env_vars.py`

The Sentry DSN is loaded from environment variables. While not hardcoded in source, `send_default_pii=False` is correctly set (`server.py:227`).

### 16. `tcpdump` Installed in Production Container
**Location**: `Dockerfile:98`

A network packet capture tool in production could aid attackers who gain container access.

### 17. Default Allow List Includes `grep` and `cat`
**Location**: `holmes/plugins/toolsets/bash/common/default_lists.py`

The `grep` command in the core allow list can read arbitrary files:
```bash
grep -r "password" /etc/  # Allowed by default
```

The `cat` command in the extended allow list can read any file:
```bash
cat /etc/shadow  # Allowed in extended mode
```

This is documented and intentional for the extended list, but `grep` in the core list could be exploited to read sensitive files in non-containerized environments.

---

## Positive Security Controls Observed

1. **Bash command validation** (`holmes/plugins/toolsets/bash/validation.py`): Well-implemented prefix-based validation with proper parsing via `bashlex`.
2. **Parameter sanitization** (`holmes/core/tools.py:135-146`): `shlex.quote()` applied to all tool parameters before shell execution.
3. **ulimit protection** (`holmes/utils/memory_limit.py`): Memory limits applied to subprocess execution.
4. **Sentry PII protection**: `send_default_pii=False` correctly configured.
5. **Read-only tool design**: Tools are documented as read-only by design.
6. **Deny list takes precedence over allow list**: Proper security ordering in validation.
7. **Compound command detection**: Bash validation properly flags `for`/`while`/`if` statements for approval.

---

## Summary

| Severity | Count |
|----------|-------|
| Critical | 3 |
| High | 4 |
| Medium | 6 |
| Low | 4 |

The most impactful findings are the lack of API authentication (#1), Jinja2 template injection risk (#2), and the wide-open CORS policy (#3). The bash command validation system is well-designed but the minimal hardcoded block list (#10) and `BASH_TOOL_UNSAFE_ALLOW_ALL` escape hatch (#4) weaken its effectiveness.
