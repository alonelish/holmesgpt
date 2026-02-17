# Policy Filtering

HolmesGPT supports policy-based filtering to control which tools can be called and with what parameters. This enables security-conscious deployments where you want to restrict Holmes's access to specific namespaces, resource types, or tools.

## Configuration

Add a `policy` section to your Holmes configuration:

```yaml
# ~/.holmes/config.yaml
policy:
  enabled: true
  default: allow  # or "deny" for whitelist mode
  rules:
    - name: rule-name
      match: ["tool_pattern_*"]
      allow_if:
        python: 'expression'  # OR bash: 'command'
      message: "Custom denial message"
```

## Policy Semantics

### Default Behavior

The `default` setting controls what happens when a tool call matches **no rules**:

| `default` | Behavior | Use Case |
|-----------|----------|----------|
| `allow` (default) | Unmatched tools are allowed | Blacklist mode - block specific tools/params |
| `deny` | Unmatched tools are denied | Whitelist mode - only allow specific tools |

### Rule Evaluation

When a tool call matches one or more rules:

1. **ALL matching rules must pass** (AND semantics)
2. Each rule's `allow_if` condition must evaluate to `True`
3. If any rule's condition fails, the call is denied

### Required Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Unique rule identifier |
| `match` | No | Tool patterns (fnmatch), defaults to `["*"]` |
| `allow_if` | Yes | Condition block with `python:` or `bash:` |
| `message` | No | Custom denial message |
| `vars` | No | Additional variables for the expression |
| `rate_limit` | No | Rate limiting configuration (see below) |

## Condition Types

The `allow_if` field requires exactly one of `python:` or `bash:`:

### Python Conditions

Use `python:` for fast, in-process evaluation with Python expressions:

```yaml
allow_if:
  python: 'params.get("namespace", "").startswith("team-a-")'
```

### Bash Conditions

Use `bash:` for external checks like Kubernetes RBAC verification:

```yaml
allow_if:
  bash: 'kubectl auth can-i get {{ params.kind }} -n {{ params.namespace }} --as={{ context.user_email }}'
```

Bash conditions:

- Exit code 0 = allow, non-zero = deny
- Stderr is captured as the denial message
- 10-second timeout per command
- Support Jinja2-style templating (see below)

## Python Expression Language

### Available Variables

| Variable | Description |
|----------|-------------|
| `tool` | Name of the tool being called |
| `params` | Dictionary of parameters passed to the tool |
| `context` | Additional context (user, team, etc.) |

### Built-in Functions

**String helpers:**

| Function | Description |
|----------|-------------|
| `match(pattern, string)` | Glob pattern matching (fnmatch) |
| `regex(pattern, string)` | Regular expression matching |
| `startswith(s, prefix)` | String prefix check |
| `endswith(s, suffix)` | String suffix check |
| `contains(s, sub)` | Substring check |

**HTTP helpers (for API-based permission checks):**

| Function | Description |
|----------|-------------|
| `http_get(url, params=None, headers=None, auth=None)` | Make HTTP GET request, returns JSON dict |
| `http_post(url, json_data=None, headers=None, auth=None)` | Make HTTP POST request, returns JSON dict |
| `env(name, default="")` | Get environment variable |

The `auth` parameter accepts either a tuple `(username, password)` for basic auth or a string for bearer token.

Standard Python functions are also available: `len`, `str`, `int`, `bool`, `list`, `dict`, `any`, `all`, `min`, `max`, etc.

## Bash Template Syntax

Bash conditions support Jinja2-style templating:

| Template | Description |
|----------|-------------|
| `{{ tool }}` | Tool name |
| `{{ params.key }}` | Parameter value |
| `{{ context.key }}` | Context value |
| `{{ vars.key }}` | Rule variable |
| `{{ params.key \| default:"value" }}` | With default value |
| `{{ params.key \| quote }}` | Shell-escaped value |

## Rate Limiting

Rules can include a `rate_limit` to restrict how many times matching tools can be called within a sliding time window. This is useful for throttling potentially destructive actions.

```yaml
rate_limit:
  window: "30m"        # Sliding time window
  max_total: 50        # Max total calls in window (all groups combined)
  max_per:             # Max calls per group
    key: "params.namespace"
    limit: 3
```

### Rate Limit Fields

| Field | Required | Description |
|-------|----------|-------------|
| `window` | Yes | Sliding time window (`30s`, `5m`, `1h`, `24h`, `1d`, or compound like `1h30m`) |
| `max_total` | No* | Max total calls within the window |
| `max_per.key` | No* | Dotted path to group by (e.g., `params.namespace`, `context.cluster`) |
| `max_per.limit` | No* | Max calls per group within the window |

*At least one of `max_total` or `max_per` is required.

### How It Works

1. Rate limits are checked **after** `allow_if` passes - denied calls don't count against the limit
2. Uses a sliding window - old calls expire as time passes
3. Each rule tracks its own counters independently
4. When both `max_total` and `max_per` are set, both limits are enforced
5. State is in-memory and resets when the process restarts

## Examples

### Blacklist Mode (Default Allow)

Restrict kubectl to specific namespaces, block bash:

```yaml
policy:
  default: allow
  rules:
    # Only allow team-a namespaces for kubectl
    - name: team-namespaces
      match: ["kubectl_*"]
      allow_if:
        python: 'params.get("namespace", "").startswith("team-a-") or params.get("namespace") is None'
      message: "Only team-a namespaces are allowed"

    # Block bash entirely
    - name: no-bash
      match: ["bash/*"]
      allow_if:
        python: "False"
      message: "Bash commands are disabled"
```

### Whitelist Mode (Default Deny)

Only allow specific tools:

```yaml
policy:
  default: deny
  rules:
    # Allow prometheus tools
    - name: allow-prometheus
      match: ["prometheus_*"]
      allow_if:
        python: "True"

    # Allow read-only kubectl with namespace constraint
    - name: allow-kubectl-read
      match: ["kubectl_get_*", "kubectl_describe", "kubectl_logs"]
      allow_if:
        python: 'params.get("namespace", "").startswith("team-a-")'
```

### Kubernetes RBAC Integration

Use bash conditions to delegate to Kubernetes RBAC:

```yaml
policy:
  default: deny
  rules:
    # Allow kubectl if user has K8s RBAC permission
    - name: user-rbac-check
      match: ["kubectl_*"]
      allow_if:
        bash: 'kubectl auth can-i get {{ params.kind | default:"pods" }} -n {{ params.namespace | default:"default" }} --as={{ context.user_email }}'
      message: "User does not have RBAC permission for this operation"
```

### Confluence Page Access Control

Check if a user can access a Confluence page before allowing the tool call. Both Python and Bash approaches are shown below - choose the one that fits your setup.

**Option A: Python with HTTP helpers (recommended)**

Uses the built-in `http_get` and `http_post` functions for cleaner syntax:

```yaml
policy:
  rules:
    - name: confluence-access-check
      match: ["confluence_get_page", "confluence_search"]
      allow_if:
        python: |
          http_post(
            env("CONFLUENCE_URL") + "/wiki/rest/api/content/" + str(params.get("page_id")) + "/permission/check",
            json_data={
              "subject": {"type": "user", "identifier": context.get("user_account_id")},
              "operation": "read"
            },
            auth=(env("CONFLUENCE_USER"), env("CONFLUENCE_TOKEN"))
          ).get("hasPermission", False)
      message: "User does not have access to this Confluence page"
```

If you need to resolve user email to account ID first:

```yaml
policy:
  rules:
    - name: confluence-access-check
      match: ["confluence_get_page"]
      allow_if:
        python: |
          (account := http_get(
            env("CONFLUENCE_URL") + "/wiki/rest/api/search/user",
            params={"cql": "type=user and email=" + context.get("user_email", "")},
            auth=(env("CONFLUENCE_USER"), env("CONFLUENCE_TOKEN"))
          ).get("results", [{}])[0].get("accountId"))
          and http_post(
            env("CONFLUENCE_URL") + "/wiki/rest/api/content/" + str(params.get("page_id")) + "/permission/check",
            json_data={"subject": {"type": "user", "identifier": account}, "operation": "read"},
            auth=(env("CONFLUENCE_USER"), env("CONFLUENCE_TOKEN"))
          ).get("hasPermission", False)
      message: "User does not have access to this Confluence page"
```

**Option B: Bash with curl**

Uses shell commands for environments where you prefer external tools:

```yaml
policy:
  rules:
    - name: confluence-access-check
      match: ["confluence_get_page"]
      allow_if:
        bash: |
          ACCOUNT_ID=$(curl -s -u "${CONFLUENCE_USER}:${CONFLUENCE_TOKEN}" \
            "${CONFLUENCE_URL}/wiki/rest/api/search/user?cql=type=user%20and%20email={{ context.user_email | quote }}" \
            | jq -r '.results[0].accountId') && \
          [ -n "$ACCOUNT_ID" ] && [ "$ACCOUNT_ID" != "null" ] && \
          curl -s -u "${CONFLUENCE_USER}:${CONFLUENCE_TOKEN}" \
            -X POST -H "Content-Type: application/json" \
            -d "{\"subject\":{\"type\":\"user\",\"identifier\":\"$ACCOUNT_ID\"},\"operation\":\"read\"}" \
            "${CONFLUENCE_URL}/wiki/rest/api/content/{{ params.page_id }}/permission/check" \
            | jq -e '.hasPermission == true'
      message: "User does not have access to this Confluence page"
```

### Block Sensitive Resources

Prevent access to secrets and RBAC resources:

```yaml
policy:
  rules:
    - name: no-sensitive-resources
      match: ["kubectl_*"]
      allow_if:
        python: 'params.get("kind", "").lower() not in blocked_kinds'
      vars:
        blocked_kinds: ["secret", "serviceaccount", "clusterrole", "clusterrolebinding"]
      message: "Access to sensitive resources is blocked"
```

### Role-Based Access (Using Context)

Restrict production access based on user role:

```yaml
policy:
  rules:
    - name: prod-admin-only
      match: ["kubectl_exec", "kubectl_delete"]
      allow_if:
        python: 'not params.get("namespace", "").startswith("prod-") or context.get("role") == "admin"'
      message: "Only admins can exec/delete in production"
```

### Multi-Tenant Isolation

Teams can only access their own namespaces:

```yaml
policy:
  rules:
    - name: tenant-isolation
      match: ["kubectl_*"]
      allow_if:
        python: |
          params.get("namespace") is None or
          params.get("namespace", "").startswith(context.get("team", "") + "-") or
          params.get("namespace") in ["shared", "monitoring"]
      message: "You can only access your team's namespaces"
```

### Layered Constraints

Combine multiple rules for layered security:

```yaml
policy:
  rules:
    # Constraint 1: namespace
    - name: namespace-constraint
      match: ["kubectl_*"]
      allow_if:
        python: 'params.get("namespace", "").startswith("team-a-")'

    # Constraint 2: no secrets
    - name: resource-constraint
      match: ["kubectl_*"]
      allow_if:
        python: 'params.get("kind") != "secret"'

    # Constraint 3: no exec
    - name: no-exec
      match: ["kubectl_exec"]
      allow_if:
        python: "False"
```

With layered constraints, a tool call must pass **all** matching rules. In this example, `kubectl_get` with `namespace=team-a-prod` and `kind=pod` would pass both constraint 1 and 2, so it's allowed. But `kubectl_get` with `kind=secret` would fail constraint 2, even if the namespace is correct.

### Rate Limiting Destructive Actions

Limit how often destructive tools can be called:

```yaml
policy:
  rules:
    - name: limit-destructive-actions
      match: ["kubectl_delete*", "kubectl_exec"]
      allow_if:
        python: "True"
      rate_limit:
        window: "30m"
        max_total: 50
        max_per:
          key: "params.namespace"
          limit: 3
```

This allows at most 50 total destructive kubectl calls per 30-minute window, with a maximum of 3 per namespace. Calls that exceed either limit are denied.

### Rate Limiting Per Cluster

Limit actions across clusters:

```yaml
policy:
  rules:
    - name: limit-per-cluster
      match: ["kubectl_*"]
      allow_if:
        python: "True"
      rate_limit:
        window: "1h"
        max_per:
          key: "context.cluster"
          limit: 10
```

### Rate Limiting Combined with Access Control

Rate limits compose with `allow_if` conditions. The `allow_if` check runs first - denied calls don't count against the rate limit:

```yaml
policy:
  rules:
    - name: limited-prod-access
      match: ["kubectl_exec", "kubectl_delete*"]
      allow_if:
        python: 'params.get("namespace", "").startswith("prod-")'
      rate_limit:
        window: "1h"
        max_total: 10
        max_per:
          key: "params.namespace"
          limit: 3
      message: "Only production namespaces allowed"
```

In this example, calls to non-production namespaces are denied by `allow_if` and don't consume rate limit quota. Only successful calls to production namespaces count toward the limits.

## Helm Configuration

For Kubernetes deployments, configure policy in your Helm values:

```yaml
# values.yaml
additionalEnvVars:
  - name: HOLMES_CONFIG
    value: |
      policy:
        default: deny
        rules:
          - name: allow-read-tools
            match: ["kubectl_get_*", "prometheus_*"]
            allow_if:
              python: "True"
```

Or mount a ConfigMap with your policy configuration.

## Debugging

Policy decisions are logged at INFO level:

```
Policy denied tool 'kubectl_get' with params {'namespace': 'kube-system'}: Only team-a namespaces are allowed
Policy denied tool 'bash/run_command': no matching rules (default: deny)
```

To debug policy rules, check:

1. Tool name matches the `match` patterns (uses fnmatch)
2. Expression evaluates correctly with given `params`
3. For whitelist mode, ensure tools have matching rules
4. For bash conditions, verify the command works manually

## Security Considerations

- Policy filtering is enforced at the application level, not the API level
- For Kubernetes, also configure RBAC on the Holmes ServiceAccount for defense-in-depth
- Python expressions run in a sandboxed environment (simpleeval) but avoid exposing untrusted input
- Bash conditions execute shell commands - ensure proper input validation via the `quote` filter
- Use `default: deny` for highest security (whitelist mode)
