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
| `registry` | Container registry | `robustadev` |
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

## Toolset Secrets {: #toolset-secrets }

Toolset configuration is rendered into a Kubernetes **ConfigMap**, not a Secret. If you put credentials directly in `values.yaml`, they end up in plaintext in the ConfigMap:

```yaml
# BAD - credentials stored in plaintext in a ConfigMap
toolsets:
  prometheus/metrics:
    enabled: true
    config:
      prometheus_url: "https://prometheus.example.com"
      additional_headers:
        Authorization: "Bearer my-secret-token"  # Visible to anyone who can read ConfigMaps
```

To keep credentials out of the ConfigMap, use the `{{ env.VAR }}` pattern. This lets you store secrets in a Kubernetes Secret, inject them as environment variables, and reference them in toolset config. HolmesGPT resolves `{{ env.VAR }}` placeholders at runtime.

**Step 1: Create a Kubernetes Secret**

```bash
kubectl create secret generic prometheus-auth \
  --from-literal=token="Bearer my-secret-token" \
  -n <namespace>
```

**Step 2: Reference the secret in `values.yaml`**

```yaml
additionalEnvVars:
  - name: PROMETHEUS_AUTH_TOKEN
    valueFrom:
      secretKeyRef:
        name: prometheus-auth
        key: token

toolsets:
  prometheus/metrics:
    enabled: true
    config:
      prometheus_url: "https://prometheus.example.com"
      additional_headers:
        Authorization: "{{ env.PROMETHEUS_AUTH_TOKEN }}"
```

The ConfigMap will contain the literal string `{{ env.PROMETHEUS_AUTH_TOKEN }}` instead of the actual token. At startup, HolmesGPT resolves it from the environment variable.

This pattern works for any toolset config field. Here are more examples:

```yaml
additionalEnvVars:
  - name: GRAFANA_API_KEY
    valueFrom:
      secretKeyRef:
        name: grafana-credentials
        key: api-key
  - name: ELASTICSEARCH_API_KEY
    valueFrom:
      secretKeyRef:
        name: elasticsearch-credentials
        key: api-key

toolsets:
  grafana/dashboards:
    enabled: true
    config:
      api_url: "https://grafana.example.com"
      api_key: "{{ env.GRAFANA_API_KEY }}"
  elasticsearch/data:
    enabled: true
    config:
      api_url: "https://es.example.com:443"
      api_key: "{{ env.ELASTICSEARCH_API_KEY }}"
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
