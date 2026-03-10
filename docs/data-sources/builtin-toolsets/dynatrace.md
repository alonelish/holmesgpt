# Dynatrace (MCP)

The Dynatrace MCP server brings real-time observability data from Dynatrace into Holmes. It enables Holmes to query logs, metrics, traces, and problems using Dynatrace Query Language (DQL), investigate incidents, and analyze application performance.

## Overview

Dynatrace provides two MCP server deployment options:

- **Remote MCP server**: Connect directly to your Dynatrace environment with no local setup
- **Local MCP server**: Run via `npx` as a stdio process, then expose to Holmes via HTTP

Holmes connects to the Dynatrace MCP server using the `streamable-http` transport mode.

## Prerequisites

1. A Dynatrace Platform environment (e.g., `https://abc12345.apps.dynatrace.com`)
2. An API token or OAuth credentials with the required scopes

**Required API token scopes:**

- `app-engine:apps:run`
- `storage:buckets:read`
- `storage:logs:read`
- `storage:metrics:read`

Additional scopes for specific features:

- `automation:workflows:read` — workflow access
- `document:documents:read` — notebook and dashboard access
- `davis-copilot:nl2dql:execute` — natural language to DQL conversion

See the [Dynatrace MCP server documentation](https://docs.dynatrace.com/docs/dynatrace-intelligence/dynatrace-intelligence-integrations/dynatrace-mcp) for the full list of scopes.

## Configuration

### Option 1: Remote MCP Server (Recommended)

Connect directly to Dynatrace's hosted MCP endpoint — no local server required.

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**:

    ```yaml
    mcp_servers:
      dynatrace:
        description: "Dynatrace observability platform"
        config:
          url: "https://YOUR_ENVIRONMENT_ID.apps.dynatrace.com/platform-reserved/mcp-gateway/v0.1/servers/dynatrace-mcp/mcp"
          mode: streamable-http
          headers:
            Authorization: "Bearer <YOUR_API_TOKEN>"
        llm_instructions: "Use Dynatrace to investigate application performance issues, query logs and metrics with DQL, analyze distributed traces, and review active problems."
    ```

    Replace:

    - `YOUR_ENVIRONMENT_ID` with your Dynatrace environment ID
    - `<YOUR_API_TOKEN>` with your Dynatrace API token

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Robusta Helm Chart"

    **Create a Kubernetes secret with your Dynatrace token:**

    ```bash
    kubectl create secret generic dynatrace-mcp-token \
      --from-literal=token=<YOUR_API_TOKEN> \
      -n <NAMESPACE>
    ```

    **Add the following to your `generated_values.yaml`:**

    ```yaml
    holmes:
      additionalEnvVars:
        - name: DYNATRACE_API_TOKEN
          valueFrom:
            secretKeyRef:
              name: dynatrace-mcp-token
              key: token

      mcp_servers:
        dynatrace:
          description: "Dynatrace observability platform"
          config:
            url: "https://YOUR_ENVIRONMENT_ID.apps.dynatrace.com/platform-reserved/mcp-gateway/v0.1/servers/dynatrace-mcp/mcp"
            mode: streamable-http
            headers:
              Authorization: "Bearer {{ env.DYNATRACE_API_TOKEN }}"
          llm_instructions: "Use Dynatrace to investigate application performance issues, query logs and metrics with DQL, analyze distributed traces, and review active problems."
    ```

    Then deploy:

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

### Option 2: Local MCP Server

Run the Dynatrace MCP server locally and expose it as an HTTP endpoint.

**Start the server:**

```bash
npx -y @dynatrace-oss/dynatrace-mcp-server@latest --http --port 3000 --host 0.0.0.0
```

Set the required environment variables before running:

```bash
export DT_ENVIRONMENT="https://YOUR_ENVIRONMENT_ID.apps.dynatrace.com"
export DT_PLATFORM_TOKEN="<YOUR_API_TOKEN>"
```

=== "Holmes CLI"

    Add the following to **~/.holmes/config.yaml**:

    ```yaml
    mcp_servers:
      dynatrace:
        description: "Dynatrace observability platform"
        config:
          url: "http://localhost:3000/mcp"
          mode: streamable-http
        llm_instructions: "Use Dynatrace to investigate application performance issues, query logs and metrics with DQL, analyze distributed traces, and review active problems."
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Robusta Helm Chart"

    Deploy the Dynatrace MCP server as a sidecar or separate pod, then configure Holmes to connect:

    ```yaml
    holmes:
      mcp_servers:
        dynatrace:
          description: "Dynatrace observability platform"
          config:
            url: "http://dynatrace-mcp:3000/mcp"
            mode: streamable-http
          llm_instructions: "Use Dynatrace to investigate application performance issues, query logs and metrics with DQL, analyze distributed traces, and review active problems."
    ```

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

## Testing the Connection

```bash
holmes ask "List the active problems in Dynatrace"
```

## Common Use Cases

```
What are the top errors in the payment service over the last 24 hours?
```

```
Show me the slowest transactions for the checkout service
```

```
Are there any active problems affecting the production environment?
```

```
Query Dynatrace for logs containing 'connection timeout' in the last hour
```

```
What is the error rate trend for the API gateway over the past week?
```

## Cost Considerations

DQL queries against Dynatrace Grail may incur costs based on data scanned. To manage consumption:

- Start with shorter time ranges (12-24 hours)
- Use the `DT_GRAIL_QUERY_BUDGET_GB` environment variable (default: 1000 GB) to set a session budget

## Additional Resources

- [Dynatrace MCP Server Documentation](https://docs.dynatrace.com/docs/dynatrace-intelligence/dynatrace-intelligence-integrations/dynatrace-mcp)
- [Dynatrace MCP Server on GitHub](https://github.com/dynatrace-oss/dynatrace-mcp)
