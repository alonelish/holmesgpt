# Sentry (MCP)

The Sentry MCP server provides access to Sentry's error tracking, issue management, and observability platform. It enables Holmes to investigate application errors, analyze crash reports, and explore issue trends directly from your Sentry organization.

## Overview

Unlike other MCP addons that deploy their own server pods, the Sentry MCP connects to Sentry's **hosted MCP server** at `https://mcp.sentry.dev/mcp`. This means no additional pods are deployed in your cluster - Holmes communicates directly with Sentry's service using your authentication token.

The Sentry MCP provides access to 16+ tools across several categories:

- **Organizations, Projects, Teams**: Explore your Sentry organization structure
- **Issues & Events**: Search, analyze, and manage error issues with full event details
- **DSNs**: Retrieve Data Source Names for SDK configuration
- **Releases**: Track and manage release information
- **AI Analysis**: Leverage Sentry's Seer AI for automated issue investigation

## Prerequisites

Before enabling the Sentry MCP, you need a Sentry User Auth Token.

### Creating a Sentry Auth Token

1. Go to [sentry.io/settings/account/api/auth-tokens/](https://sentry.io/settings/account/api/auth-tokens/)
2. Click **Create New Token**
3. Give it a descriptive name (e.g., "Holmes MCP Integration")
4. Select the following scopes:
   - ✅ **org:read** - Read organization data
   - ✅ **project:read** - Read project data
   - ✅ **project:write** - Write project data
   - ✅ **team:read** - Read team data
   - ✅ **team:write** - Write team data
   - ✅ **event:write** - Write event data
5. Click **Create Token**
6. **Copy the token immediately** - it won't be shown again

## Configuration

=== "Holmes CLI"

    For CLI usage, configure the Sentry MCP in your `~/.holmes/config.yaml`:

    ```yaml
    mcp_servers:
      sentry:
        description: "Sentry MCP Server - access error tracking, issue management, and observability data"
        config:
          url: "https://mcp.sentry.dev/mcp"
          mode: "streamable-http"
          headers:
            Authorization: "Bearer {{env.SENTRY_AUTH_TOKEN}}"
    ```

    Set the environment variable before running Holmes:

    ```bash
    export SENTRY_AUTH_TOKEN="sntryu_your_token_here"
    holmes ask "What are the most recent errors in my Sentry project?"
    ```

    Alternatively, you can pass a custom toolset file:

    **sentry_toolset.yaml:**
    ```yaml
    mcp_servers:
      sentry:
        description: "Sentry MCP Server"
        config:
          url: "https://mcp.sentry.dev/mcp"
          mode: "streamable-http"
          headers:
            Authorization: "Bearer {{env.SENTRY_AUTH_TOKEN}}"
    ```

    ```bash
    export SENTRY_AUTH_TOKEN="sntryu_your_token_here"
    holmes ask -t sentry_toolset.yaml "List my Sentry organizations"
    ```

=== "Holmes Helm Chart"

    ### Step 1: Create the Kubernetes Secret

    ```bash
    kubectl create secret generic sentry-auth-token \
      --from-literal=token=<YOUR_SENTRY_AUTH_TOKEN> \
      -n <NAMESPACE>
    ```

    ### Step 2: Enable Sentry MCP in values.yaml

    ```yaml
    mcpAddons:
      sentry:
        enabled: true
        auth:
          secretName: "sentry-auth-token"
    ```

    ### Step 3: Deploy or Upgrade Holmes

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    ### Step 1: Create the Kubernetes Secret

    ```bash
    kubectl create secret generic sentry-auth-token \
      --from-literal=token=<YOUR_SENTRY_AUTH_TOKEN> \
      -n <NAMESPACE>
    ```

    ### Step 2: Enable Sentry MCP in generated_values.yaml

    ```yaml
    holmes:
      mcpAddons:
        sentry:
          enabled: true
          auth:
            secretName: "sentry-auth-token"
    ```

    ### Step 3: Deploy or Upgrade Robusta

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

## Self-Hosted Sentry

If you're using a self-hosted Sentry instance, configure the URL to point to your instance's MCP endpoint:

=== "Holmes CLI"

    ```yaml
    mcp_servers:
      sentry:
        description: "Self-hosted Sentry MCP Server"
        config:
          url: "https://sentry.mycompany.com/mcp"
          mode: "streamable-http"
          headers:
            Authorization: "Bearer {{env.SENTRY_AUTH_TOKEN}}"
    ```

=== "Helm Charts"

    ```yaml
    mcpAddons:
      sentry:
        enabled: true
        auth:
          secretName: "sentry-auth-token"
        url: "https://sentry.mycompany.com/mcp"
    ```

## Available Tools

The Sentry MCP provides comprehensive access to Sentry's platform capabilities:

| Category | Description |
|----------|-------------|
| **Organizations** | List and explore Sentry organizations |
| **Projects** | Access project settings and configuration |
| **Teams** | View team memberships and permissions |
| **Issues** | Search, filter, and analyze error issues |
| **Events** | Get detailed event data, breadcrumbs, and stack traces |
| **DSNs** | Retrieve Data Source Names for SDK configuration |
| **Releases** | Track release information and deployment history |
| **Seer AI** | Automated issue analysis and root cause detection |

## Testing the Connection

After configuring Sentry MCP, verify it's working:

### Test 1: List Organizations

```bash
holmes ask "List my Sentry organizations"
```

### Test 2: Search for Issues

```bash
holmes ask "What are the most frequent errors in the last 24 hours?"
```

### Test 3: Investigate a Specific Error

```bash
holmes ask "Find issues related to 'NullPointerException' in my project"
```

## Common Use Cases

### Investigating Application Errors

```
"We're seeing increased 500 errors. What's happening in Sentry?"
```

Holmes will:

1. List your projects and identify the relevant one
2. Search for recent issues with 5xx status codes
3. Analyze error frequency and affected users
4. Provide details on the most impactful issues
5. Suggest potential root causes

### Release Impact Analysis

```
"Did our latest release introduce any new errors?"
```

Holmes will:

1. Identify the most recent release
2. Compare error rates before and after the release
3. Find new issues that appeared after deployment
4. Check for regressions in previously resolved issues

### Error Trend Analysis

```
"Show me the trend of authentication errors this week"
```

Holmes will:

1. Search for authentication-related issues
2. Analyze frequency over the specified time period
3. Identify patterns or anomalies
4. Correlate with deployments or configuration changes

### Debugging with Stack Traces

```
"I got this error in production: TypeError at /api/users line 42"
```

Holmes will:

1. Search for matching issues in Sentry
2. Retrieve the full stack trace and breadcrumbs
3. Show the sequence of events leading to the error
4. Provide context from similar historical issues

## Troubleshooting

### Authentication Errors

**Problem:** Getting 401 Unauthorized errors

**Solution:** Verify your auth token is valid and has the required scopes:

```bash
# Test the token directly
curl -H "Authorization: Bearer $SENTRY_AUTH_TOKEN" \
  https://sentry.io/api/0/organizations/
```

If the curl fails, regenerate the token with the correct scopes.

### No Data Returned

**Problem:** Holmes reports no issues found but you know there are errors

**Solutions:**

1. Verify the organization and project names are correct
2. Check if the token has access to the specific project
3. Ensure the time range in your query matches when errors occurred

### Rate Limiting

**Problem:** Getting 429 Too Many Requests errors

**Solution:** Sentry has API rate limits. If you're hitting them:

1. Reduce the frequency of queries
2. Use more specific filters to reduce result sets
3. Consider upgrading your Sentry plan for higher limits

### Connection Timeouts

**Problem:** Requests to Sentry MCP are timing out

**Solutions:**

1. Check your network connectivity to `mcp.sentry.dev`
2. Verify your cluster can reach external HTTPS endpoints
3. Check if there are any firewall rules blocking outbound traffic

```bash
# Test connectivity from within your cluster
kubectl run test-curl --rm -it --image=curlimages/curl -- \
  curl -I https://mcp.sentry.dev/mcp
```

## Security Best Practices

1. **Use minimal scopes**: Only grant the scopes your integration actually needs
2. **Rotate tokens regularly**: Update your auth token periodically
3. **Use Kubernetes secrets**: Never commit tokens to version control
4. **Monitor token usage**: Check Sentry's audit log for unusual activity
5. **Limit project access**: If possible, create tokens with access to specific projects only

## Additional Resources

- [Sentry MCP Documentation](https://docs.sentry.io/product/sentry-mcp/)
- [Sentry Auth Tokens](https://docs.sentry.io/account/auth-tokens/)
- [Sentry API Reference](https://docs.sentry.io/api/)
