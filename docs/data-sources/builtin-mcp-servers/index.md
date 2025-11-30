# Built-in MCP Servers

HolmesGPT includes built-in MCP (Model Context Protocol) servers that can be deployed alongside HolmesGPT using the Helm chart. These servers are pre-configured and automatically integrated with HolmesGPT when enabled.

## Available Built-in MCP Servers

- **[AWS MCP Server](aws.md)** - Provides direct access to AWS APIs for investigating AWS-related issues, including CloudWatch logs, EC2 instances, RDS databases, EKS clusters, and more.

## Enabling Built-in MCP Servers

Built-in MCP servers are configured through the `mcpAddons` section in your Helm values file. Each server can be enabled independently and will automatically connect to HolmesGPT when deployed.

```yaml
mcpAddons:
  aws:
    enabled: true
    # ... configuration
```

When enabled, the Helm chart automatically:
- Deploys the MCP server as a separate Kubernetes deployment
- Creates the necessary Service, ConfigMap, and ServiceAccount resources
- Configures HolmesGPT to connect to the MCP server
- Sets up network policies for security (if enabled)

The MCP server appears as a toolset in HolmesGPT, allowing it to use the server's capabilities during investigations.
