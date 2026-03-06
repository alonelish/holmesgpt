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

### 8. Indirect Prompt Injection via Tool Outputs
**Severity**: HIGH
**Location**: `holmes/core/tool_calling_llm.py:639`

Tool outputs from kubectl, Prometheus, Grafana, log systems, etc. are passed directly to the LLM as tool response messages without any sanitization. An attacker who controls data in monitored systems (e.g., Kubernetes annotations, pod names, log messages, Grafana dashboard titles) could embed prompt injection payloads that the LLM would process as instructions.

Additionally, runbook content (`holmes/plugins/toolsets/runbook/runbook_fetcher.py:175-208`) is explicitly granted "ABSOLUTE PRIORITY" in the system prompt (`_runbooks_instructions.jinja2:9-14`), making it a high-value injection target. A compromised runbook entry in Supabase can override all LLM behavior.

**Recommendation**: Consider output sanitization for known injection patterns. Reduce the privilege level of runbook instructions. Add integrity checks for runbook content.

### 9. Kubernetes Secrets Readable via Default Allow List
**Severity**: HIGH
**Location**: Default bash allow list includes `kubectl get`, `kubectl describe`

The LLM can run `kubectl get secret -o yaml` or `kubectl describe secret`, returning base64-encoded secret data. This data flows into the LLM context and then into responses sent to Slack, Jira, or web UIs. No redaction of sensitive data patterns occurs before data enters the LLM context.

**Recommendation**: Add `kubectl get secret` and `kubectl describe secret` to the deny list, or implement output filtering to redact base64-encoded secret values before they enter the LLM context.

### 10. SSRF in Internet Toolset
**Severity**: HIGH
**Location**: `holmes/plugins/toolsets/internet/internet.py:92-96`

```python
response = requests.get(url, headers=headers, timeout=...)
```

The internet toolset allows the LLM to fetch arbitrary URLs with no restriction on internal/private IP addresses (e.g., `http://169.254.169.254/` for cloud metadata, `http://localhost`, `http://10.*`). A manipulated LLM (via prompt injection in a webpage or alert) could probe internal services or exfiltrate cloud instance metadata.

Note: The HTTP toolset (`http_toolset.py`) correctly implements URL whitelisting, but the internet toolset does not.

**Recommendation**: Block requests to private/internal IP ranges (RFC 1918, link-local, loopback) and cloud metadata endpoints.

---

## Medium Severity Findings

### 11. Missing HTTP Request Timeouts
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

### 12. Grafana-Proxied Prometheus Defaults to `verify_ssl=False`
**Severity**: MEDIUM
**Location**: `holmes/plugins/toolsets/prometheus/prometheus.py:199`

```python
class GrafanaProxiedPrometheusConfig:
    verify_ssl: bool = False
```

When Prometheus is accessed through a Grafana proxy, SSL verification is disabled by default, enabling MITM attacks on this traffic.

**Recommendation**: Default to `verify_ssl=True` and document how to disable it if needed.

### 13. Hardcoded Bash Block List is Minimal
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

### 14. Sensitive Data Exposure in Error Responses
**Severity**: MEDIUM
**Location**: `server.py:292-294, 372-373, 531-533`

```python
except Exception as e:
    raise HTTPException(status_code=500, detail=str(e))
```

Internal exception details are returned directly to the client. This can leak internal paths, database connection strings, or other sensitive implementation details.

**Recommendation**: Return generic error messages to clients. Log detailed errors server-side only.

### 15. Header Passthrough to MCP Servers
**Severity**: MEDIUM
**Location**: `server.py:386-411`

The `extract_passthrough_headers()` function forwards most request headers to MCP servers. While `authorization`, `cookie`, and `set-cookie` are blocked by default, other potentially sensitive headers (e.g., `X-API-Key`, `X-Auth-Token`, custom auth headers) are forwarded.

**Recommendation**: Use an allowlist approach instead of a blocklist for header passthrough.

### 16. SQL Injection via LLM-Provided Queries
**Severity**: MEDIUM
**Location**: `holmes/plugins/toolsets/database/database.py:287`

The database toolset executes raw SQL from the LLM via `sqlalchemy.text(sql)`. Three defense layers exist:
1. Regex-based read-only validation (blocks write keywords)
2. `SET TRANSACTION READ ONLY` (silently fails on unsupported dialects)
3. Row limit (max 200 rows)

However, regex-based SQL validation has known bypass potential with database-specific syntax (e.g., PostgreSQL `COPY TO PROGRAM`, MySQL `LOAD_FILE()`, `INTO OUTFILE`). When `read_only=False` is configured, no SQL validation occurs at all.

**Recommendation**: Use database-level read-only users/roles as the primary guard. Consider parameterized queries where possible.

### 17. MCP Tools Forwarded Without Local Validation
**Severity**: MEDIUM
**Location**: `holmes/plugins/toolsets/mcp/toolset_mcp.py:275`

LLM-specified parameters are forwarded directly to MCP servers via `session.call_tool(self.name, params)` with no local validation. MCP servers are external and their tools could have write/delete capabilities.

**Recommendation**: Add parameter validation or schema enforcement for MCP tool calls.

### 18. MCP Header Templates Expose Full Environment
**Severity**: MEDIUM
**Location**: `holmes/plugins/toolsets/mcp/toolset_mcp.py:449`

```python
context["env"] = os.environ
```

The full process environment is passed to Jinja2 templates for MCP extra_headers. If MCP config is controlled by a less-trusted party, they could exfiltrate arbitrary environment variables (including API keys) by referencing `{{ env.SECRET_KEY }}` in header templates, sending values as HTTP headers to the MCP server.

**Recommendation**: Pass only explicitly-listed environment variables to MCP templates, not the entire `os.environ`.

### 19. Inconsistent `SecretStr` Usage for Credentials
**Severity**: MEDIUM
**Location**: Multiple files

| Field | File | Line |
|-------|------|------|
| `alertmanager_password` | `holmes/config.py` | 65 |
| HTTP auth `password`/`token`/`value` | `holmes/plugins/toolsets/http/http_toolset.py` | 28-37 |
| Database `connection_url` | `holmes/plugins/toolsets/database/database.py` | 91 |

These credentials use plain `str` instead of `SecretStr`, meaning they appear in `.dict()`, `repr()`, logs, and error messages. The rest of the codebase correctly uses `SecretStr` for secrets (e.g., `api_key`, `jira_api_key`, `slack_token`).

Additionally, `SecretStr` values are unwrapped to plain strings in `DefaultLLM.__init__` (`holmes/core/llm.py:157`), losing protection from that point on.

**Recommendation**: Use `SecretStr` consistently for all credential fields. Avoid unwrapping to plain strings where possible.

### 20. CLI Arguments Expose Secrets in Process Listing
**Severity**: MEDIUM
**Location**: `holmes/main.py:136-139, 417-418`

`--api-key`, `--slack-token`, `--alertmanager-password` are accepted as CLI arguments. Values passed on the command line are visible in `ps` output to all users on the system.

**Recommendation**: Accept secrets only via environment variables or config files, not CLI arguments.

### 21. No Rate Limiting on API Endpoints
**Severity**: MEDIUM
**Location**: `server.py`

No per-user, per-account, or per-time-period rate limit exists on any endpoint. The only limit is `max_steps` (default 40), capping tool-calling loop iterations per investigation. A single client can trigger unlimited LLM API calls consuming API credits.

**Recommendation**: Add rate limiting middleware to the FastAPI server.

### 22. Interactive Mode Executes User Commands via Shell
**Severity**: MEDIUM
**Location**: `holmes/interactive.py:813-814`

```python
result = subprocess.run(bash_command, shell=True, capture_output=True, text=True)
```

The interactive CLI mode allows users to run arbitrary bash commands. While this is expected for a CLI tool, the commands are executed with `shell=True` and no validation.

**Recommendation**: This is acceptable for a CLI tool (the user already has shell access), but should be documented as intentional behavior.

---

## Low Severity Findings

### 23. Binary Permissions Set to 777
**Location**: `Dockerfile:43, 58`

```dockerfile
chmod 777 kube-lineage
chmod 777 argocd
```

Binaries should use `755` (owner execute, world read+execute) not `777` (world writable).

### 24. Path Traversal in `builtin://` Prompt Loader
**Location**: `holmes/plugins/prompts/__init__.py:19`

```python
path = os.path.join(THIS_DIR, prompt[len("builtin://"):])
```

A value like `builtin://../../etc/passwd` resolves outside the prompts directory. The `file://` handler also has no path restriction. Both are operator-controlled inputs (CLI args or config file), but any compromise of config allows arbitrary file reads. Note: The runbook fetcher correctly uses `os.path.realpath()` containment checks.

### 25. ReDoS via LLM-Provided Regex Filters
**Location**: `holmes/plugins/toolsets/kubernetes_logs.py:715, 727`

LLM-provided filter parameters are compiled as regex via `re.compile()`. A crafted regex could cause catastrophic backtracking (ReDoS). The code falls back to substring matching on `re.error`, but does not handle valid-but-slow patterns.

### 26. Hardcoded Sentry DSN in Environment
**Location**: `holmes/common/env_vars.py`

The Sentry DSN is loaded from environment variables. While not hardcoded in source, `send_default_pii=False` is correctly set (`server.py:227`).

### 27. `tcpdump` Installed in Production Container
**Location**: `Dockerfile:98`

A network packet capture tool in production could aid attackers who gain container access.

### 28. Default Allow List Includes `grep` and `cat`
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
8. **Safe YAML loading**: All YAML loading uses `yaml.safe_load()`. No instances of unsafe YAML deserialization.
9. **No pickle usage**: No pickle deserialization anywhere in the codebase.
10. **HTTP toolset whitelisting**: Proper endpoint whitelisting with host/path/method restrictions.
11. **Runbook path traversal protection**: `os.path.realpath()` with directory containment checks.
12. **SecretStr for core credentials**: Main config uses `SecretStr` for API keys, tokens.
13. **Pre-commit private key detection**: `detect-private-key` hook configured.
14. **Datadog header sanitization**: Headers containing "key" are redacted before logging.
15. **MCP request context redaction**: `ToolInvokeContext.model_dump()` redacts sensitive headers.
16. **Tool context window limiter**: Prevents oversized tool responses from consuming the LLM context.
17. **Restricted tools gated behind runbook fetch**: Some tools only available after runbook authorization.

---

## Summary

| Severity | Count |
|----------|-------|
| Critical | 2 |
| High | 6 |
| Medium | 12 |
| Low | 8 |
| **Total** | **28** |

### Top Priority Recommendations

1. **Add API authentication** (#1) - The single most impactful change. Without it, all other server-side security controls can be bypassed by any network-reachable entity.
2. **Sandbox Jinja2 templates** (#2) - Use `SandboxedEnvironment` for all template rendering involving LLM-provided parameters.
3. **Fix CORS policy** (#3) - Remove `allow_origins=["*"]` with `allow_credentials=True` from the experimental server that ships in Docker.
4. **Restrict tool output data** (#8, #9) - Add output filtering for sensitive data (secrets, credentials) before tool results enter the LLM context.
5. **Block internal SSRF** (#10) - Add private IP range blocking to the internet toolset.
6. **Run container as non-root** (#7) - Add a `USER` directive to the main Dockerfile.
7. **Add request timeouts** (#11) - Prevent DoS via hung connections to external services.
8. **Use SecretStr consistently** (#19) - Fix the inconsistent credential handling across toolset configs.
