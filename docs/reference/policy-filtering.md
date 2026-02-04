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
      when: 'expression'
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
2. Each rule's `when` expression must evaluate to `True`
3. If any rule's `when` is `False`, the call is denied

### Required Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Unique rule identifier |
| `match` | No | Tool patterns (fnmatch), defaults to `["*"]` |
| `when` | Yes | Python expression that must be `True` to allow |
| `message` | No | Custom denial message |
| `vars` | No | Additional variables for the expression |

## Expression Language

The `when` field uses Python expressions evaluated in a sandboxed environment.

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
      when: 'params.get("namespace", "").startswith("team-a-") or params.get("namespace") is None'
      message: "Only team-a namespaces are allowed"

    # Block bash entirely
    - name: no-bash
      match: ["bash/*"]
      when: "False"
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
      when: "True"

    # Allow read-only kubectl with namespace constraint
    - name: allow-kubectl-read
      match: ["kubectl_get_*", "kubectl_describe", "kubectl_logs"]
      when: 'params.get("namespace", "").startswith("team-a-")'
```

### Block Sensitive Resources

Prevent access to secrets and RBAC resources:

```yaml
policy:
  rules:
    - name: no-sensitive-resources
      match: ["kubectl_*"]
      when: 'params.get("kind", "").lower() not in blocked_kinds'
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
      when: 'not params.get("namespace", "").startswith("prod-") or context.get("role") == "admin"'
      message: "Only admins can exec/delete in production"
```

### Multi-Tenant Isolation

Teams can only access their own namespaces:

```yaml
policy:
  rules:
    - name: tenant-isolation
      match: ["kubectl_*"]
      when: |
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
      when: 'params.get("namespace", "").startswith("team-a-")'

    # Constraint 2: no secrets
    - name: resource-constraint
      match: ["kubectl_*"]
      when: 'params.get("kind") != "secret"'

    # Constraint 3: no exec
    - name: no-exec
      match: ["kubectl_exec"]
      when: "False"
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
            when: "True"
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

## Security Considerations

- Policy filtering is enforced at the application level, not the API level
- For Kubernetes, also configure RBAC on the Holmes ServiceAccount for defense-in-depth
- Expressions run in a sandboxed environment but avoid exposing untrusted input
- Use `default: deny` for highest security (whitelist mode)
