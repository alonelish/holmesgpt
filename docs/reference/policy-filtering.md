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

| Function | Description |
|----------|-------------|
| `match(pattern, string)` | Glob pattern matching (fnmatch) |
| `regex(pattern, string)` | Regular expression matching |
| `startswith(s, prefix)` | String prefix check |
| `endswith(s, suffix)` | String suffix check |
| `contains(s, sub)` | Substring check |

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
