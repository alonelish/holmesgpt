# AWS MCP Server

The AWS MCP (Model Context Protocol) server is an optional add-on that provides HolmesGPT with direct access to AWS APIs through a secure, read-only interface. This enables HolmesGPT to investigate AWS-related issues by querying CloudWatch logs, EC2 instances, RDS databases, EKS clusters, and other AWS services directly.

## Overview

The AWS MCP server is deployed as a separate Kubernetes deployment alongside HolmesGPT. It exposes AWS APIs through the Model Context Protocol, allowing HolmesGPT to execute AWS CLI commands programmatically during investigations. The server is packaged as a Docker container using Supergateway to expose the stdio-based AWS MCP as an HTTP API, making it accessible as a remote MCP server within Kubernetes.

## Architecture

```
Holmes → Remote MCP (HTTP API) → Supergateway Wrapper → AWS MCP Server → AWS APIs
                                        ↓
                          Running in Kubernetes with IRSA
                          (IAM Roles for Service Accounts)
```

The MCP server runs in a separate pod and communicates with HolmesGPT via Kubernetes Service networking. It uses IAM Roles for Service Accounts (IRSA) for secure, credential-free AWS API access.

## Prerequisites

- **EKS Cluster** with IRSA support (or use AWS profiles for non-EKS clusters)

  - **Authentication**: Choose one:
    - **IRSA**: IAM role with read-only AWS permissions, OIDC provider configured, service account annotation
    - **AWS Profiles**: Kubernetes secret containing AWS credentials file

## Configuration

The AWS MCP server is configured through the `mcpAddons.aws` section in your Helm values file. **No additional Holmes configuration is needed** - the Helm chart automatically configures HolmesGPT to connect to the MCP server when enabled.

!!! tip "IRSA vs Profiles"
    Use IRSA for single AWS account access on EKS (credential-free). Use Profiles for multiple accounts or when IRSA isn't available.

=== "Holmes Helm Chart"

    === "IRSA Config"

        ```yaml
        # values.yaml
        mcpAddons:
          aws:
            enabled: true
            
            # Service account configuration for IRSA
            serviceAccount:
              create: true
              name: "aws-api-mcp-sa"
              annotations:
                eks.amazonaws.com/role-arn: "arn:aws:iam::<account-id>:role/aws-mcp-server-role"
            
            # Container image configuration
            image: "aws-api-mcp-server:1.0.1"
            registry: "us-central1-docker.pkg.dev/genuine-flight-317411/devel"
            
            # AWS configuration
            config:
              region: "us-east-1"
              readOnlyMode: true
              namespace: ""  # Defaults to release namespace if empty
            
            # Resource limits
            resources:
              requests:
                memory: "512Mi"
                cpu: "250m"
              limits:
                memory: "1Gi"
                cpu: "500m"
            
            # Network policy (recommended)
            networkPolicy:
              enabled: true
        ```

    === "Config File"

        **Create Kubernetes secrets before deploying:**

        **1. Create credentials secret:**

        ```bash
        kubectl create secret generic aws-mcp-credentials \
          --from-file=credentials=~/.aws/credentials \
          -n <namespace>
        ```

        Example credentials file (`~/.aws/credentials`):
        ```ini
        [default]
        aws_access_key_id = AKIAIOSFODNN7EXAMPLE
        aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY

        [production]
        aws_access_key_id = AKIAI44QH8DHBEXAMPLE
        aws_secret_access_key = je7MtGbClwBF/2Zp9Utk/h3yCo8nvbEXAMPLEKEY
        ```

        **2. Create config secret (optional, for assume role):**

        ```bash
        kubectl create secret generic aws-mcp-config-file \
          --from-file=config=~/.aws/config \
          -n <namespace>
        ```

        Example config file (`~/.aws/config`):
        ```ini
        [default]
        region = us-east-1

        [profile production]
        role_arn = arn:aws:iam::123456789012:role/ProductionRole
        source_profile = default
        region = us-west-2
        ```

        **Then configure Helm values:**

        ```yaml
        # values.yaml
        mcpAddons:
          aws:
            enabled: true
            
            # Service account (optional when using profiles)
            serviceAccount:
              create: false  # Set to false if not using IRSA
            
            image: "aws-api-mcp-server:1.0.1"
            registry: "us-central1-docker.pkg.dev/genuine-flight-317411/devel"
            
            config:
              region: "us-east-1"
              readOnlyMode: true
              namespace: ""
            
            # AWS credentials for profile support
            credentials:
              secretName: "aws-mcp-credentials"  # Kubernetes secret name
              secretKey: "credentials"  # Key in secret containing credentials file
            
            resources:
              requests:
                memory: "512Mi"
                cpu: "250m"
              limits:
                memory: "1Gi"
                cpu: "500m"
            
            networkPolicy:
              enabled: true
        ```

=== "Robusta Helm Chart"

    === "IRSA Config"

        ```yaml
        # generated_values.yaml
        holmes:
          mcpAddons:
            aws:
              enabled: true
              
              # Service account configuration for IRSA
              serviceAccount:
                create: true
                name: "aws-api-mcp-sa"
                annotations:
                  eks.amazonaws.com/role-arn: "arn:aws:iam::<account-id>:role/aws-mcp-server-role"
              
              # Container image configuration
              image: "aws-api-mcp-server:1.0.1"
              registry: "us-central1-docker.pkg.dev/genuine-flight-317411/devel"
              
              # AWS configuration
              config:
                region: "us-east-1"
                readOnlyMode: true
                namespace: ""  # Defaults to release namespace if empty
              
              # Resource limits
              resources:
                requests:
                  memory: "512Mi"
                  cpu: "250m"
                limits:
                  memory: "1Gi"
                  cpu: "500m"
              
              # Network policy (recommended)
              networkPolicy:
                enabled: true
        ```

    === "Config File"

        **Create credentials secret:**

        ```bash
        kubectl create secret generic aws-mcp-credentials \
          --from-file=credentials=~/.aws/credentials \
          -n <namespace>
        ```

        Example credentials file (`~/.aws/credentials`):
        ```ini
        [default]
        aws_access_key_id = AKIAIOSFODNN7EXAMPLE
        aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY

        [production]
        aws_access_key_id = AKIAI44QH8DHBEXAMPLE
        aws_secret_access_key = je7MtGbClwBF/2Zp9Utk/h3yCo8nvbEXAMPLEKEY
        ```

        **Then configure Helm values:**

        ```yaml
        # generated_values.yaml
        holmes:
          mcpAddons:
            aws:
              enabled: true
              
              # Service account (optional when using profiles)
              serviceAccount:
                create: false  # Set to false if not using IRSA
              
              image: "aws-api-mcp-server:1.0.1"
              registry: "us-central1-docker.pkg.dev/genuine-flight-317411/devel"
              
              config:
                region: "us-east-1"
                readOnlyMode: true
                namespace: ""
              
              # AWS credentials for profile support
              credentials:
                secretName: "aws-mcp-credentials"  # Kubernetes secret name
                secretKey: "credentials"  # Key in secret containing credentials file
              
              resources:
                requests:
                  memory: "512Mi"
                  cpu: "250m"
                limits:
                  memory: "1Gi"
                  cpu: "500m"
              
              networkPolicy:
                enabled: true
        ```

## Configuration Parameters

All available configuration parameters under `mcpAddons.aws` (or `holmes.mcpAddons.aws` for Robusta Helm Chart):

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enabled` | boolean | `false` | Enable/disable the AWS MCP server deployment |
| `serviceAccount.create` | boolean | `true` | Whether to create a new service account |
| `serviceAccount.name` | string | `"aws-api-mcp-sa"` | Name of the service account |
| `serviceAccount.annotations` | object | `{}` | Annotations for the service account (e.g., IRSA role ARN) |
| `image` | string | `"aws-api-mcp-server:1.0.1"` | Container image name (without registry) |
| `registry` | string | `"us-central1-docker.pkg.dev/genuine-flight-317411/devel"` | Container registry URL |
| `config.region` | string | `"us-east-1"` | AWS region for API calls |
| `config.readOnlyMode` | boolean | `true` | Restrict to read-only operations (recommended) |
| `config.namespace` | string | `""` | Kubernetes namespace (defaults to release namespace) |
| `credentials.secretName` | string | `""` | Kubernetes secret name containing AWS credentials file (required for profile support) |
| `credentials.secretKey` | string | `"credentials"` | Key in secret containing credentials file content |
| `resources.requests.memory` | string | `"512Mi"` | Memory request |
| `resources.requests.cpu` | string | `"250m"` | CPU request |
| `resources.limits.memory` | string | `"1Gi"` | Memory limit |
| `resources.limits.cpu` | string | `"500m"` | CPU limit |
| `networkPolicy.enabled` | boolean | `true` | Enable network policy to restrict access to Holmes pods only |
| `llmInstructions` | string | `""` | Custom LLM instructions (empty uses defaults) |
| `nodeSelector` | object | `{}` | Node selector for pod scheduling |
| `tolerations` | array | `[]` | Tolerations for pod scheduling |
| `affinity` | object | `{}` | Affinity rules for pod scheduling |

## Deployment

Once configured, deploy with Helm:

=== "Holmes Helm Chart"

    ```bash
    helm upgrade --install holmes ./helm/holmes \
      --set mcpAddons.aws.enabled=true \
      --set mcpAddons.aws.serviceAccount.annotations."eks\.amazonaws\.com/role-arn"=arn:aws:iam::<account-id>:role/aws-mcp-server-role
    ```

    Or include the configuration in your `values.yaml` file and deploy normally:

    ```bash
    helm upgrade --install holmes ./helm/holmes -f values.yaml
    ```

=== "Robusta Helm Chart"

    ```bash
    helm upgrade robusta robusta/robusta \
      --set holmes.mcpAddons.aws.enabled=true \
      --set holmes.mcpAddons.aws.serviceAccount.annotations."eks\.amazonaws\.com/role-arn"=arn:aws:iam::<account-id>:role/aws-mcp-server-role \
      --values=generated_values.yaml \
      --set clusterName=<YOUR_CLUSTER_NAME>
    ```

    Or include the configuration in your `generated_values.yaml` file and deploy normally:

    ```bash
    helm upgrade robusta robusta/robusta --values=generated_values.yaml --set clusterName=<YOUR_CLUSTER_NAME>
    ```

## Verification

After deployment, verify the MCP server is running:

```bash
# Check pod status
kubectl get pods -l app=holmes-aws-mcp

# Check service
kubectl get svc -l app=holmes-aws-mcp

# View logs
kubectl logs -l app=holmes-aws-mcp --tail=50
```

