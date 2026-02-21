# Helm ✓

--8<-- "snippets/enabled_by_default.md"

Helm read-only commands (`helm list`, `helm get`, `helm status`, `helm history`) are included in the [bash toolset's](bash.md) core allowlist and work automatically when the `helm` CLI is available.

No additional configuration is needed beyond having Helm installed.

## Common Use Cases

```bash
holmes ask "What Helm releases are deployed in the cluster?"
```

```bash
holmes ask "What values were used to deploy the nginx release in the production namespace?"
```

```bash
holmes ask "Show the revision history for the my-app Helm release"
```
