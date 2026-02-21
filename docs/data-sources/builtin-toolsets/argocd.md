# ArgoCD

ArgoCD read-only commands are included in the [bash toolset's](bash.md) core allowlist. Once the `argocd` CLI is configured with authentication, HolmesGPT can fetch the status, deployment history, and configuration of ArgoCD applications.

![Holmes ArgoCD Demo](../../assets/Holmes_ArgoCD_demo.gif)

## Prerequisites

### Generating an ArgoCD token
This integration requires an `ARGOCD_AUTH_TOKEN` environment variable. Generate an auth token by following [these steps](https://argo-cd.readthedocs.io/en/stable/user-guide/commands/argocd_account_generate-token/).

### Adding a Read-only Policy to ArgoCD
HolmesGPT requires specific permissions to access ArgoCD data. Add the permissions below to your ArgoCD RBAC configuration.

Edit the RBAC ConfigMap: `kubectl edit configmap argocd-rbac-cm -n argocd`

```yaml
# Add this to the data section of your argocd-rbac-cm configmap.
# Creates a 'holmesgpt' user with read-only permissions for troubleshooting.
data:
  policy.default: role:readonly
  policy.csv: |
    p, role:admin, *, *, *, allow
    p, role:admin, accounts, apiKey, *, allow
    p, holmesgpt, accounts, apiKey, holmesgpt, allow
    p, holmesgpt, projects, get, *, allow
    p, holmesgpt, applications, get, *, allow
    p, holmesgpt, repositories, get, *, allow
    p, holmesgpt, clusters, get, *, allow
    p, holmesgpt, applications, manifests, */*, allow
    p, holmesgpt, applications, resources, */*, allow
    g, admin, role:admin
```

## Configuration

In addition to setting permissions and generating an auth token, you will need to tell HolmesGPT how to connect to the server. This can be done two ways:

1. **Using port forwarding**. This is the recommended approach if your ArgoCD is inside your Kubernetes cluster.
2. **Setting the env var** `ARGOCD_SERVER`. This is the recommended approach if your ArgoCD is reachable through a public DNS.

### 1. Port Forwarding

This is the recommended approach if your ArgoCD is inside your Kubernetes cluster.

=== "Holmes CLI"

    Set the following environment variables:

    ```bash
    export ARGOCD_AUTH_TOKEN="<your-argocd-token>"
    export ARGOCD_OPTS="--port-forward --port-forward-namespace <your_argocd_namespace> --server <your_server_address> --grpc-web"
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Robusta Helm Chart"

    ```yaml
    holmes:
        customClusterRoleRules:
            - apiGroups: [""]
              resources: ["pods/portforward"]
              verbs: ["create"]
        additionalEnvVars:
            - name: ARGOCD_AUTH_TOKEN
              value: "<your-argocd-token>"
            - name: ARGOCD_OPTS
              value: "--port-forward --port-forward-namespace <your_argocd_namespace> --server <your_server_address> --grpc-web"
    ```

    --8<-- "snippets/helm_upgrade_command.md"

!!! note

    For in-cluster address, use the cluster DNS. For example: `--port-forward --port-forward-namespace argocd --server argocd-server.argocd.svc.cluster.local --insecure --grpc-web`

    - Add `--insecure` to work with self-signed certificates
    - Change the namespace `--port-forward-namespace <your_argocd_namespace>` to the namespace in which your ArgoCD service is deployed
    - The option `--grpc-web` in `ARGOCD_OPTS` prevents some connection errors from leaking into the tool responses and provides a cleaner output for HolmesGPT

### 2. Server URL

This is the recommended approach if your ArgoCD is reachable through a public DNS.

=== "Holmes CLI"

    Set the following environment variables:

    ```bash
    export ARGOCD_AUTH_TOKEN="<your-argocd-token>"
    export ARGOCD_SERVER="argocd.example.com"
    ```

    To test, run:

    ```bash
    holmes ask "Which ArgoCD applications are failing and why?"
    ```

=== "Robusta Helm Chart"

    ```yaml
    holmes:
        additionalEnvVars:
            - name: ARGOCD_AUTH_TOKEN
              value: "<your-argocd-token>"
            - name: ARGOCD_SERVER
              value: "argocd.example.com"
    ```

    --8<-- "snippets/helm_upgrade_command.md"

!!! note

    In production, always use a Kubernetes secret instead of hardcoding the token value in your Helm values.

## Common Use Cases

```bash
holmes ask "Which ArgoCD applications are failing and why?"
```

```bash
holmes ask "Is the demo-app out of sync? Show me the diff."
```

```bash
holmes ask "List all ArgoCD projects and their applications"
```
