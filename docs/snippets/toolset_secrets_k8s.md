!!! tip "Kubernetes deployments: avoid storing credentials in plaintext"
    When using the Helm chart, use `{{ env.VAR }}` references instead of hardcoding secrets. See [Toolset Secrets](../../reference/helm-configuration.md#toolset-secrets) for details.
