# Helm Configuration

Configuration reference for HolmesGPT Helm chart.

**Quick Links:**

- [Installation Tutorial](../installation/kubernetes-installation.md) - Step-by-step setup guide
- [values.yaml](https://github.com/HolmesGPT/holmesgpt/blob/master/helm/holmes/values.yaml) - Complete configuration reference
- [HTTP API Reference](../reference/http-api.md) - Test your deployment

## Basic Configuration

```yaml
# values.yaml
# Image settings
image: holmes:0.0.0
registry: robustadev

# Logging level
logLevel: INFO

# send exceptions to sentry
enableTelemetry: true

# Resource limits
resources:
  requests:
    cpu: 100m
    memory: 1024Mi
  limits:
    memory: 1024Mi

# Enabled/disable/customize specific toolsets
toolsets:
  kubernetes/core:
    enabled: true
  kubernetes/logs:
    enabled: true
  robusta:
    enabled: true
  internet:
    enabled: true
  prometheus/metrics:
    enabled: true
  ...
```

## Configuration Options

### Essential Settings

| Parameter | Description | Default |
|-----------|-------------|---------|
| `additionalEnvVars` | Environment variables (API keys, etc.) | `[]` |
| `toolsets` | Enable/disable specific toolsets | (see values.yaml) |
| `modelList` | Configure multiple AI models for UI selection. See [Using Multiple Providers](../ai-providers/using-multiple-providers.md) | `{}` |
| `openshift` | Enable OpenShift compatibility mode | `false` |
| `image` | HolmesGPT image name | `holmes:0.0.0` |
| `registry` | Container registry for HolmesGPT image | `robustadev` |
| `imagePullSecrets` | List of secrets for pulling from private registries | `[]` |
| `logLevel` | Log level (DEBUG, INFO, WARN, ERROR) | `INFO` |
| `enableTelemetry` | Send exception reports to sentry | `true` |
| `certificate` | Base64 encoded custom CA certificate for outbound HTTPS requests (e.g., LLM API via proxy) | `""` |
| `sentryDSN` | Sentry DSN for telemetry | (see values.yaml) |

#### API Key Configuration

The most important configuration is setting up API keys for your chosen AI provider:

```yaml
additionalEnvVars:
- name: OPENAI_API_KEY
  value: "your-api-key"
# Or load from secret:
# - name: OPENAI_API_KEY
#   valueFrom:
#     secretKeyRef:
#       name: holmes-secrets
#       key: openai-api-key
```

#### Toolset Configuration

Control which capabilities HolmesGPT has access to:

```yaml
toolsets:
  kubernetes/core:
    enabled: true      # Core Kubernetes functionality
  kubernetes/logs:
    enabled: true      # Kubernetes logs access
  robusta:
    enabled: true      # Robusta platform integration
  internet:
    enabled: true      # Internet access for documentation
  prometheus/metrics:
    enabled: true      # Prometheus metrics access
```

### Service Account Configuration

```yaml
# Create service account (default: true)
createServiceAccount: true

# Use custom service account name
customServiceAccountName: ""

# Service account settings
serviceAccount:
  imagePullSecrets: []
  annotations: {}

# Custom RBAC rules
customClusterRoleRules: []
```

For detailed information about the required Kubernetes permissions, see [Kubernetes Permissions](kubernetes-permissions.md).

### Resource Configuration

```yaml
resources:
  requests:
    cpu: 100m
    memory: 1024Mi
  limits:
    cpu: 100m        # Optional CPU limit
    memory: 1024Mi
```

### Toolset Configuration

Enable or disable specific toolsets:

```yaml
toolsets:
  kubernetes/core:
    enabled: true      # Core Kubernetes functionality
  kubernetes/logs:
    enabled: true      # Kubernetes logs access
  robusta:
    enabled: true      # Robusta platform integration
  internet:
    enabled: true      # Internet access for documentation
  prometheus/metrics:
    enabled: true      # Prometheus metrics access
```

### Advanced Configuration

#### Scheduling

```yaml
# Node selection
# nodeSelector:
#   kubernetes.io/os: linux

# Pod affinity/anti-affinity
affinity: {}

# Tolerations
tolerations: []

# Priority class
priorityClassName: ""
```

#### Additional Configuration

```yaml
# Additional environment variables
additionalEnvVars: []
additional_env_vars: []  # Legacy, use additionalEnvVars instead

# Image pull secrets
imagePullSecrets: []

# Additional volumes
additionalVolumes: []

# Additional volume mounts
additionalVolumeMounts: []

# OpenShift compatibility mode
openshift: false

# Account creation
enableAccountsCreate: true

# MCP servers configuration
mcp_servers: {}

# Model list configuration for multiple AI providers (UI only)
# See: https://holmesgpt.dev/ai-providers/using-multiple-providers/
modelList: {}
```

## Private Docker Registries

If your organization uses an internal or private Docker registry, configure HolmesGPT to pull images from your registry instead of the default public registry.

### Basic Configuration

```yaml
# values.yaml
# Use your internal registry
registry: your-internal-registry.company.com/holmesgpt

# Image name (usually unchanged)
image: holmes:0.0.0
```

The full image path will be: `your-internal-registry.company.com/holmesgpt/holmes:0.0.0`

### Authentication with Private Registries

For registries requiring authentication, create an image pull secret and reference it:

**Step 1: Create the image pull secret**

```bash
# Using Docker credentials
kubectl create secret docker-registry holmes-registry-secret \
  --docker-server=your-internal-registry.company.com \
  --docker-username=your-username \
  --docker-password=your-password \
  --docker-email=your-email@company.com \
  -n <namespace>

# Or from existing Docker config
kubectl create secret generic holmes-registry-secret \
  --from-file=.dockerconfigjson=$HOME/.docker/config.json \
  --type=kubernetes.io/dockerconfigjson \
  -n <namespace>
```

**Step 2: Configure Helm values**

```yaml
# values.yaml
registry: your-internal-registry.company.com/holmesgpt
image: holmes:0.0.0

# Reference the image pull secret
imagePullSecrets:
  - name: holmes-registry-secret

# Also configure for the service account if needed
serviceAccount:
  imagePullSecrets:
    - name: holmes-registry-secret
```

### MCP Addon Registries

MCP (Model Context Protocol) addons have their own registry settings. Configure each addon's registry individually:

```yaml
# values.yaml
# Main Holmes image
registry: your-internal-registry.company.com/holmesgpt
imagePullSecrets:
  - name: holmes-registry-secret

# MCP Addon registries
mcpAddons:
  aws:
    enabled: true
    registry: "your-internal-registry.company.com/mcp-servers"
    image: "aws-api-mcp-server:1.0.1"

  gcp:
    enabled: true
    registry: "your-internal-registry.company.com/mcp-servers"
    gcloud:
      image: "gcloud-cli-mcp:1.0.7"
    observability:
      image: "gcloud-observability-mcp:1.0.0"
    storage:
      image: "gcloud-storage-mcp:1.0.0"

  azure:
    enabled: true
    registry: "your-internal-registry.company.com/mcp-servers"
    image: "azure-cli-mcp:1.0.1"

  github:
    enabled: true
    registry: "your-internal-registry.company.com/mcp-servers"
    image: "github-mcp:1.0.0"

  mariadb:
    enabled: true
    registry: "your-internal-registry.company.com/mcp-servers"
    image: "mariadb-http-mcp-minimal:1.0.5"
```

### Mirroring Images

To use HolmesGPT with a private registry, first mirror the required images:

```bash
# Mirror main Holmes image
docker pull robustadev/holmes:0.0.0
docker tag robustadev/holmes:0.0.0 your-internal-registry.company.com/holmesgpt/holmes:0.0.0
docker push your-internal-registry.company.com/holmesgpt/holmes:0.0.0

# Mirror MCP addon images (if using)
docker pull us-central1-docker.pkg.dev/genuine-flight-317411/devel/aws-api-mcp-server:1.0.1
docker tag us-central1-docker.pkg.dev/genuine-flight-317411/devel/aws-api-mcp-server:1.0.1 \
  your-internal-registry.company.com/mcp-servers/aws-api-mcp-server:1.0.1
docker push your-internal-registry.company.com/mcp-servers/aws-api-mcp-server:1.0.1
```

### Robusta Platform Integration

When deploying HolmesGPT as part of the Robusta platform, the registry settings are configured under the `holmes` section. The Robusta platform Helm chart also supports global registry settings:

```yaml
# Robusta platform values.yaml
globalConfig:
  # Global registry applies to all components unless overridden
  registry: your-internal-registry.company.com

# Holmes-specific overrides (optional)
holmes:
  registry: your-internal-registry.company.com/holmesgpt
  imagePullSecrets:
    - name: holmes-registry-secret
```

!!! note "Robusta Platform Registry Settings"
    When using the full Robusta platform, refer to the [Robusta documentation](https://docs.robusta.dev/){:target="_blank"} for additional registry configuration options including `globalConfig.registry` which applies to all platform components.

## Example Configurations

### Minimal Setup

```yaml
# values.yaml
image: holmes:0.0.0
registry: robustadev
logLevel: INFO
enableTelemetry: false

resources:
  requests:
    cpu: 100m
    memory: 512Mi
  limits:
    memory: 512Mi

toolsets:
  kubernetes/core:
    enabled: true
  kubernetes/logs:
    enabled: true
  robusta:
    enabled: false
  internet:
    enabled: false
  prometheus/metrics:
    enabled: false
```

### Multiple AI Providers Setup

```yaml
# values.yaml
additionalEnvVars:
  - name: OPENAI_API_KEY
    valueFrom:
      secretKeyRef:
        name: holmes-secrets
        key: openai-api-key
  - name: ANTHROPIC_API_KEY
    valueFrom:
      secretKeyRef:
        name: holmes-secrets
        key: anthropic-api-key
  - name: AWS_ACCESS_KEY_ID
    valueFrom:
      secretKeyRef:
        name: holmes-secrets
        key: aws-access-key-id
  - name: AWS_SECRET_ACCESS_KEY
    valueFrom:
      secretKeyRef:
        name: holmes-secrets
        key: aws-secret-access-key

modelList:
  gpt-4.1:
    api_key: "{{ env.OPENAI_API_KEY }}"
    model: openai/gpt-4.1
    temperature: 0
  claude-sonnet-4:
    api_key: "{{ env.ANTHROPIC_API_KEY }}"
    model: anthropic/claude-sonnet-4-20250514
    temperature: 1
    thinking:
      budget_tokens: 10000
      type: enabled
  bedrock-sonnet-4:
    aws_access_key_id: "{{ env.AWS_ACCESS_KEY_ID }}"
    aws_region_name: us-east-1
    aws_secret_access_key: "{{ env.AWS_SECRET_ACCESS_KEY }}"
    model: bedrock/anthropic.claude-sonnet-4-20250514-v1:0
    temperature: 1
    thinking:
      budget_tokens: 10000
      type: enabled
```


### OpenShift Setup

```yaml
# values.yaml
openshift: true
createServiceAccount: true

resources:
  requests:
    cpu: 100m
    memory: 1024Mi
  limits:
    memory: 1024Mi

toolsets:
  kubernetes/core:
    enabled: true
  kubernetes/logs:
    enabled: true
```

### Private Registry Setup

```yaml
# values.yaml
# Use internal registry
registry: registry.internal.company.com/holmesgpt
image: holmes:0.0.0

# Authentication for private registry
imagePullSecrets:
  - name: internal-registry-secret

serviceAccount:
  imagePullSecrets:
    - name: internal-registry-secret

# AI provider configuration
additionalEnvVars:
  - name: OPENAI_API_KEY
    valueFrom:
      secretKeyRef:
        name: holmes-secrets
        key: openai-api-key

modelList:
  gpt-4.1:
    api_key: "{{ env.OPENAI_API_KEY }}"
    model: openai/gpt-4.1
    temperature: 0

# MCP addons with private registry
mcpAddons:
  aws:
    enabled: true
    registry: "registry.internal.company.com/mcp-servers"
    image: "aws-api-mcp-server:1.0.1"
```

## Configuration Validation

```bash
# Validate configuration
helm template holmesgpt robusta/holmes -f values.yaml

# Dry run installation
helm install holmesgpt robusta/holmes -f values.yaml --dry-run

# Check syntax
yamllint values.yaml
```

## Complete Reference

For the complete and up-to-date configuration reference, see the actual [`values.yaml`](https://github.com/HolmesGPT/holmesgpt/blob/master/helm/holmes/values.yaml) file in the repository.
