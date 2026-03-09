# PagerDuty (MCP)

The PagerDuty MCP server provides access to PagerDuty for incident management, on-call schedules, escalation policies, and service monitoring. It enables Holmes to investigate active incidents, check who is on-call, review change events, and analyze service dependencies.

## Prerequisites

Before configuring the PagerDuty MCP server, you need a PagerDuty User API Token.

1. Log in to your PagerDuty account
2. Go to **My Profile** → **User Settings** → **API Access**
3. Click **Create API User Token**
4. **Copy the token immediately** - it won't be shown again

!!! note "EU Region"
    If your PagerDuty account is in the EU region, you'll need to set the API host to `https://api.eu.pagerduty.com`.

## Configuration

=== "Holmes CLI"

    For CLI usage, you can configure Holmes to connect to PagerDuty MCP using stdio mode (runs the server as a local subprocess).

    **Step 1: Install the PagerDuty MCP server**

    ```bash
    pip install pagerduty-mcp
    ```

    **Step 2: Configure Holmes CLI**

    Add the MCP server configuration to **~/.holmes/config.yaml**:

    ```yaml
    mcp_servers:
      pagerduty:
        description: "PagerDuty incident management and on-call schedules"
        config:
          mode: stdio
          command: uvx
          args:
            - "pagerduty-mcp"
          env:
            PAGERDUTY_USER_API_KEY: "{{ env.PAGERDUTY_USER_API_KEY }}"
    ```

    Set the environment variable before running Holmes:

    ```bash
    export PAGERDUTY_USER_API_KEY=<YOUR_PAGERDUTY_API_TOKEN>
    ```

    --8<-- "snippets/toolset_refresh_warning.md"

=== "Holmes Helm Chart"

    First, create a Kubernetes secret with your PagerDuty API token:

    ```bash
    kubectl create secret generic pagerduty-mcp-token \
      --from-literal=token=<YOUR_PAGERDUTY_API_TOKEN> \
      -n <NAMESPACE>
    ```

    Then add the following to your `values.yaml`:

    ```yaml
    mcpAddons:
      pagerduty:
        enabled: true
        auth:
          secretName: "pagerduty-mcp-token"
    ```

    For EU region accounts:

    ```yaml
    mcpAddons:
      pagerduty:
        enabled: true
        auth:
          secretName: "pagerduty-mcp-token"
        config:
          apiHost: "https://api.eu.pagerduty.com"
    ```

    Then deploy or upgrade your Holmes installation:

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    First, create a Kubernetes secret with your PagerDuty API token:

    ```bash
    kubectl create secret generic pagerduty-mcp-token \
      --from-literal=token=<YOUR_PAGERDUTY_API_TOKEN> \
      -n <NAMESPACE>
    ```

    Then add the following to your `generated_values.yaml`:

    ```yaml
    holmes:
      mcpAddons:
        pagerduty:
          enabled: true
          auth:
            secretName: "pagerduty-mcp-token"
    ```

    Then deploy or upgrade your Robusta installation:

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

## Available Tools

The PagerDuty MCP server provides 70+ tools organized by domain:

| Category | Tools |
|----------|-------|
| Incidents | List, create, get details, add notes, manage responders, run workflows |
| Alerts | List alerts for incidents |
| Services | List, create, update, get details |
| Schedules | List, create, update, get users, create overrides |
| Escalation Policies | List, get details |
| On-Call | Get current on-call users |
| Teams | List, create, update, manage members |
| Users | List, get details |
| Event Orchestrations | List, get details, update routers |
| Status Pages | Create posts, list, get details |
| Log Entries | List entries |
| Change Events | List change events |
| Alert Grouping | Full CRUD operations |

## Testing the Connection

```bash
holmes ask "List the most recent PagerDuty incidents"
```

## Common Use Cases

```bash
holmes ask "What PagerDuty incidents are currently triggered or acknowledged?"
```

```bash
holmes ask "Who is currently on-call for the production escalation policy?"
```

```bash
holmes ask "Show me the recent change events that might be related to the latest incident"
```

```bash
holmes ask "What services have had the most incidents this week?"
```

## Additional Resources

- [PagerDuty API Documentation](https://developer.pagerduty.com/api-reference/)
- [PagerDuty MCP Server](https://github.com/PagerDuty/pagerduty-mcp-server)
