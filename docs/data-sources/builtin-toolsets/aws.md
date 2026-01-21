# AWS (MCP)

The AWS MCP server gives Holmes **read-only access to any AWS API** you permit via IAM. This means Holmes can query EC2, RDS, ELB, CloudWatch, CloudTrail, S3, Lambda, Cost Explorer, and hundreds of other AWS services - limited only by the IAM policy you attach.

## Overview

The AWS MCP server runs as a pod in your Kubernetes cluster.

- **Helm users**: The pod is deployed automatically when you enable the addon
- **CLI users**: You deploy the pod manually to your cluster, then point Holmes at it

!!! note
    Even when using Holmes CLI locally, the AWS MCP server must run in a Kubernetes cluster. Local-only deployment is not currently supported.

## Prerequisites

**For EKS clusters (recommended)**:

- An EKS cluster with an OIDC provider enabled ([how to enable](https://docs.aws.amazon.com/eks/latest/userguide/enable-iam-roles-for-service-accounts.html))
- An IAM role configured for IRSA (IAM Roles for Service Accounts)
- The IAM role must have the HolmesGPT read-only policy attached

**For non-EKS clusters**:

- AWS access key and secret key with appropriate permissions

## Step 1: Set Up IAM Permissions

Before configuring Holmes, you need to create the IAM policy and role that grants AWS access.

### Create the IAM Policy

The AWS MCP server requires read-only permissions across AWS services. We provide a default IAM policy that covers most AWS services.

You can customize the IAM policy to restrict access, but the default policy is recommended for most users.

**Option A: Use the helper scripts (recommended)**

We provide scripts that automate the IAM setup:

1. [Enable OIDC Provider Script](https://github.com/robusta-dev/holmes-mcp-integrations/blob/master/servers/aws/enable-oidc-provider.sh) - Enables OIDC for your EKS cluster
2. [Setup IRSA Script](https://github.com/robusta-dev/holmes-mcp-integrations/blob/master/servers/aws/setup-irsa.sh) - Creates the policy and IAM role

**Option B: Create manually**

```bash
# Download the policy
curl -O https://raw.githubusercontent.com/robusta-dev/holmes-mcp-integrations/master/servers/aws/aws-mcp-iam-policy.json

# Create the IAM policy
aws iam create-policy \
  --policy-name HolmesMCPReadOnly \
  --policy-document file://aws-mcp-iam-policy.json
```

The complete policy is available on GitHub: [aws-mcp-iam-policy.json](https://github.com/robusta-dev/holmes-mcp-integrations/blob/master/servers/aws/aws-mcp-iam-policy.json)

### Create the IAM Role for IRSA

Create an IAM role that can be assumed by the Kubernetes service account.

**Service account names by installation method:**

- Holmes Helm Chart: `aws-api-mcp-sa`
- Robusta Helm Chart: `aws-api-mcp-sa`
- CLI deployment: `aws-mcp-sa` (as defined in the manifest)

```bash
# Get your OIDC provider URL
OIDC_PROVIDER=$(aws eks describe-cluster --name YOUR_CLUSTER_NAME --query "cluster.identity.oidc.issuer" --output text | sed -e "s/^https:\/\///")

# Create the trust policy
cat > trust-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/${OIDC_PROVIDER}"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "${OIDC_PROVIDER}:sub": "system:serviceaccount:YOUR_NAMESPACE:SERVICE_ACCOUNT_NAME"
        }
      }
    }
  ]
}
EOF

# Create the role
aws iam create-role \
  --role-name HolmesMCPRole \
  --assume-role-policy-document file://trust-policy.json

# Attach the policy to the role
aws iam attach-role-policy \
  --role-name HolmesMCPRole \
  --policy-arn arn:aws:iam::ACCOUNT_ID:policy/HolmesMCPReadOnly
```

**Note the role ARN** - you'll need it in the next step: `arn:aws:iam::ACCOUNT_ID:role/HolmesMCPRole`

## Step 2: Configure and Deploy

Choose your installation method:

=== "Holmes Helm Chart"

    **Step 2a: Update your values.yaml**

    Add the AWS MCP addon configuration:

    ```yaml
    mcpAddons:
      aws:
        enabled: true

        serviceAccount:
          create: true
          annotations:
            # Use the IAM role ARN from Step 1
            eks.amazonaws.com/role-arn: "arn:aws:iam::ACCOUNT_ID:role/HolmesMCPRole"

        config:
          region: "us-east-1"  # Change to your AWS region
    ```

    For additional options (resources, network policy, node selectors), see the [full chart values](https://github.com/HolmesGPT/holmesgpt/blob/master/helm/holmes/values.yaml#L75).

    **Step 2b: Deploy Holmes**

    ```bash
    helm upgrade --install holmes robusta/holmes -f values.yaml
    ```

    **Step 2c: Verify the deployment**

    ```bash
    # Check that the MCP server pod is running
    kubectl get pods -l app.kubernetes.io/name=aws-mcp-server

    # Check the logs for any errors
    kubectl logs -l app.kubernetes.io/name=aws-mcp-server
    ```

=== "Robusta Helm Chart"

    **Step 2a: Update your generated_values.yaml**

    Add the Holmes MCP addon configuration under the `holmes` section:

    ```yaml
    globalConfig:
      # Your existing Robusta configuration

    holmes:
      mcpAddons:
        aws:
          enabled: true

          serviceAccount:
            create: true
            annotations:
              # Use the IAM role ARN from Step 1
              eks.amazonaws.com/role-arn: "arn:aws:iam::ACCOUNT_ID:role/HolmesMCPRole"

          config:
            region: "us-east-1"  # Change to your AWS region
    ```

    For additional options (resources, network policy, node selectors), see the [full chart values](https://github.com/HolmesGPT/holmesgpt/blob/master/helm/holmes/values.yaml#L75).

    **Step 2b: Deploy Robusta**

    ```bash
    helm upgrade --install robusta robusta/robusta -f generated_values.yaml --set clusterName=YOUR_CLUSTER_NAME
    ```

    **Step 2c: Verify the deployment**

    ```bash
    # Check that the MCP server pod is running
    kubectl get pods -l app.kubernetes.io/name=aws-mcp-server

    # Check the logs for any errors
    kubectl logs -l app.kubernetes.io/name=aws-mcp-server
    ```

=== "Holmes CLI"

    For CLI usage, you need to deploy the AWS MCP server to your cluster, then configure Holmes to connect to it.

    **Step 2a: Create the deployment manifest**

    Create a file named `aws-mcp-deployment.yaml`:

    ```yaml
    apiVersion: v1
    kind: Namespace
    metadata:
      name: holmes-mcp
    ---
    apiVersion: v1
    kind: ServiceAccount
    metadata:
      name: aws-mcp-sa
      namespace: holmes-mcp
      annotations:
        # Use the IAM role ARN from Step 1
        eks.amazonaws.com/role-arn: "arn:aws:iam::ACCOUNT_ID:role/HolmesMCPRole"
    ---
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: aws-mcp-server
      namespace: holmes-mcp
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: aws-mcp-server
      template:
        metadata:
          labels:
            app: aws-mcp-server
        spec:
          serviceAccountName: aws-mcp-sa
          containers:
          - name: aws-mcp
            image: us-central1-docker.pkg.dev/genuine-flight-317411/devel/aws-api-mcp-server:1.0.1
            imagePullPolicy: Always
            ports:
            - containerPort: 8000
              name: http
            env:
            - name: AWS_REGION
              value: "us-east-1"  # Change to your region
            - name: AWS_DEFAULT_REGION
              value: "us-east-1"  # Change to your region
            - name: READ_OPERATIONS_ONLY
              value: "true"
            resources:
              requests:
                memory: "512Mi"
                cpu: "250m"
              limits:
                memory: "1Gi"
                cpu: "500m"
            readinessProbe:
              tcpSocket:
                port: 8000
              initialDelaySeconds: 20
              periodSeconds: 10
            livenessProbe:
              tcpSocket:
                port: 8000
              initialDelaySeconds: 30
              periodSeconds: 30
    ---
    apiVersion: v1
    kind: Service
    metadata:
      name: aws-mcp-server
      namespace: holmes-mcp
    spec:
      selector:
        app: aws-mcp-server
      ports:
      - port: 8000
        targetPort: 8000
        protocol: TCP
        name: http
    ```

    **Step 2b: Deploy to your cluster**

    ```bash
    kubectl apply -f aws-mcp-deployment.yaml
    ```

    **Step 2c: Verify the deployment**

    ```bash
    # Check that the pod is running
    kubectl get pods -n holmes-mcp

    # Check the logs for any errors
    kubectl logs -n holmes-mcp -l app=aws-mcp-server
    ```

    **Step 2d: Configure Holmes CLI**

    Add the MCP server to `~/.holmes/config.yaml`:

    ```yaml
    mcp_servers:
      aws_api:
        description: "AWS API MCP Server - comprehensive AWS service access. Allow executing any AWS CLI commands."
        url: "http://aws-mcp-server.holmes-mcp.svc.cluster.local:8000"
        llm_instructions: |
          IMPORTANT: When investigating issues related to AWS resources or Kubernetes workloads running on AWS, you MUST actively use this MCP server to gather data rather than providing manual instructions to the user.

          ## Investigation Principles

          **ALWAYS follow this investigation flow:**
          1. First, gather current state and configuration using AWS APIs
          2. Check CloudTrail for recent changes that might have caused the issue
          3. Collect metrics and logs from CloudWatch if available
          4. Analyze all gathered data before providing conclusions

          **Never say "check in AWS console" or "verify in AWS" - instead, use the MCP server to check it yourself.**

          ## Core Investigation Patterns

          ### For ANY connectivity or access issues:
          1. ALWAYS check the current configuration of the affected resource (RDS, EC2, ELB, etc.)
          2. ALWAYS examine security groups and network ACLs
          3. ALWAYS query CloudTrail for recent configuration changes
          4. Look for patterns in timing between when issues started and when changes were made

          ### When investigating database issues (RDS):
          - Get RDS instance status and configuration: `aws rds describe-db-instances --db-instance-identifier INSTANCE_ID`
          - Check security groups attached to RDS: Extract VpcSecurityGroups from the above
          - Examine security group rules: `aws ec2 describe-security-groups --group-ids SG_ID`
          - Look for recent RDS events: `aws rds describe-events --source-identifier INSTANCE_ID --source-type db-instance`
          - Check CloudTrail for security group modifications: `aws cloudtrail lookup-events --lookup-attributes AttributeKey=ResourceName,AttributeValue=SG_ID`

          Remember: Your goal is to gather evidence from AWS, not to instruct the user to gather it. Use the MCP server proactively to build a complete picture of what happened.
    ```

    **Step 2e: Port forwarding (for local testing only)**

    If running Holmes CLI locally (outside the cluster):

    ```bash
    kubectl port-forward -n holmes-mcp svc/aws-mcp-server 8000:8000
    ```

    Then update the URL in `~/.holmes/config.yaml`:

    ```yaml
    url: "http://localhost:8000"
    ```

## Alternative: Using Access Keys Instead of IRSA

If you're not using EKS or prefer static credentials, you can use AWS access keys for CLI deployments.

Create a secret with your credentials:

```bash
kubectl create secret generic aws-credentials \
  --from-literal=aws-access-key-id=YOUR_KEY \
  --from-literal=aws-secret-access-key=YOUR_SECRET \
  -n holmes-mcp
```

Then update the deployment manifest to reference the secret:

```yaml
env:
  - name: AWS_ACCESS_KEY_ID
    valueFrom:
      secretKeyRef:
        name: aws-credentials
        key: aws-access-key-id
  - name: AWS_SECRET_ACCESS_KEY
    valueFrom:
      secretKeyRef:
        name: aws-credentials
        key: aws-secret-access-key
```

## Multi-Account Setup

For scenarios where you need to access multiple AWS accounts from your EKS clusters, the AWS MCP server supports multi-account access using cross-account IAM roles and EKS token projection.

### When to Use Multi-Account Setup

- You have multiple AWS accounts (dev, staging, prod, etc.)
- You want pods in any cluster to access resources in target accounts
- You need centralized IAM role management across accounts
- You're using AWS Organizations or multi-account architectures

### How It Works

When multi-account mode is enabled, the MCP server:

1. Uses **EKS token projection** instead of IRSA (IAM Roles for Service Accounts)
2. Mounts an `accounts.yaml` configuration file that defines target accounts and their IAM roles
3. Uses `assume_role_with_web_identity` to assume roles in target accounts
4. Allows the LLM to specify which account to use via the `--profile` flag

### IAM Setup with setup-multi-account-iam.sh

For multi-account setup, we provide a helper script to automate the setup of cross-account OIDC providers and IAM roles:

- [Multi-Account Setup Script](https://github.com/robusta-dev/holmes-mcp-integrations/blob/master/servers/aws/setup-multi-account-iam.sh)

#### What the Script Does

For each target account, the script:

1. **Creates OIDC Providers**: Sets up OIDC providers for each cluster in the target account
2. **Creates IAM Role**: Creates a role with trust policy allowing `assume_role_with_web_identity` from all configured clusters
3. **Attaches Permissions**: Applies the read-only permissions policy to the role

This enables pods running in any of your clusters to assume roles in target accounts and access AWS resources there.

#### Configuration File

Create a YAML config file. You can download an example configuration file:

- [Example Configuration File](https://github.com/robusta-dev/holmes-mcp-integrations/blob/master/servers/aws/multi-cluster-config-example.yaml)

Example configuration:

```yaml
clusters:
  - name: prod-cluster
    region: us-east-1
    account_id: "111111111111"
    oidc_issuer_id: AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
    oidc_issuer_url: https://oidc.eks.us-east-1.amazonaws.com/id/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA

  - name: staging-cluster
    region: us-west-2
    account_id: "111111111111"
    oidc_issuer_id: BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB
    oidc_issuer_url: https://oidc.eks.us-west-2.amazonaws.com/id/BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB

kubernetes:
  namespace: default
  service_account: multi-account-mcp-sa

iam:
  role_name: EKSMultiAccountMCPRole
  policy_name: MCPReadOnlyPolicy
  session_duration: 3600

target_accounts:
  - profile: dev
    account_id: "111111111111"
    description: "Development account"
    
  - profile: prod
    account_id: "222222222222"
    description: "Production account"
```

#### Getting OIDC Issuer Information

For each cluster, you need the OIDC issuer ID and URL:

```bash
# Get OIDC issuer URL
aws eks describe-cluster --name <cluster-name> --query "cluster.identity.oidc.issuer" --output text

# Extract issuer ID from the URL
# URL format: https://oidc.eks.<region>.amazonaws.com/id/<ISSUER_ID>
```

#### Running the Setup

```bash
# Basic usage (uses default config: multi-cluster-config.yaml)
./setup-multi-account-iam.sh setup

# With custom config file
./setup-multi-account-iam.sh setup my-config.yaml

# With custom permissions file
./setup-multi-account-iam.sh setup my-config.yaml ./aws-mcp-iam-policy.json

# Verify the setup
./setup-multi-account-iam.sh verify my-config.yaml

# Teardown (removes all created resources)
./setup-multi-account-iam.sh teardown my-config.yaml
```

#### Download the Script

```bash
# Download the setup script
curl -O https://raw.githubusercontent.com/robusta-dev/holmes-mcp-integrations/master/servers/aws/setup-multi-account-iam.sh
chmod +x setup-multi-account-iam.sh

# Download example configuration file
curl -O https://raw.githubusercontent.com/robusta-dev/holmes-mcp-integrations/master/servers/aws/multi-cluster-config-example.yaml
```

### Helm Chart Configuration

Once the IAM roles are set up, configure the Helm chart to enable multi-account mode:

=== "Holmes Helm Chart"

    Add the following configuration to your `values.yaml` file:

    ```yaml
    mcpAddons:
      aws:
        enabled: true

        # AWS configuration
        config:
          region: "us-east-1"  # Your AWS region
          readOnlyMode: true

        # Multi-account configuration
        multiAccount:
          enabled: true
          profiles:
            dev:
              account_id: "111111111111"
              role_arn: "arn:aws:iam::111111111111:role/EKSMultiAccountMCPRole"
              region: "us-east-1"  # optional, defaults to the region specified in config
            prod:
              account_id: "222222222222"
              role_arn: "arn:aws:iam::222222222222:role/EKSMultiAccountMCPRole"
              region: "us-east-1"  # optional, defaults to the region specified in config
          llm_account_descriptions: |
            You must use the --profile flag to specify the account to use.
            Example: --profile dev - this is the development account and contains the development resources
            Example: --profile prod - this is the production account and contains the production resources

        # Note: When multiAccount.enabled is true, IRSA annotations are not used
        # The service account will use EKS token projection instead
        serviceAccount:
          create: true
          # annotations are ignored when multiAccount is enabled
    ```

=== "Robusta Helm Chart"

    Add the following configuration to your `generated_values.yaml`:

    ```yaml
    holmes:
      mcpAddons:
        aws:
          enabled: true

          # AWS configuration
          config:
            region: "us-east-1"  # Your AWS region
            readOnlyMode: true

          # Multi-account configuration
          multiAccount:
            enabled: true
            profiles:
              dev:
                account_id: "111111111111"
                role_arn: "arn:aws:iam::111111111111:role/EKSMultiAccountMCPRole"
                region: "us-east-1"  # optional, defaults to the region specified in config
              prod:
                account_id: "222222222222"
                role_arn: "arn:aws:iam::222222222222:role/EKSMultiAccountMCPRole"
                region: "us-east-1"  # optional, defaults to the region specified in config
            llm_account_descriptions: |
              You must use the --profile flag to specify the account to use.
              Example: --profile dev - this is the development account and contains the development resources
              Example: --profile prod - this is the production account and contains the production resources

          # Note: When multiAccount.enabled is true, IRSA annotations are not used
          # The service account will use EKS token projection instead
          serviceAccount:
            create: true
            # annotations are ignored when multiAccount is enabled
    ```

### Important Notes

- When `multiAccount.enabled` is `true`, the Helm chart automatically:
  - Creates an `accounts.yaml` ConfigMap with your account profiles
  - Mounts the accounts.yaml file at `/etc/aws/accounts.yaml` in the pod
  - Mounts the EKS service account token at `/var/run/secrets/eks.amazonaws.com/serviceaccount`
  - Skips IRSA annotations on the service account (since multi-account uses token projection)
- The `llm_account_descriptions` field is automatically appended to the LLM instructions to help guide the AI on how to use the different accounts
- Each profile can optionally specify a `region` that overrides the default region from `config.region`
- The MCP server will use the EKS token to assume roles in target accounts using `assume_role_with_web_identity`

## Capabilities

The AWS MCP server provides access to all AWS services through the AWS CLI. Common investigation patterns include:

### CloudTrail Investigation
- Query recent API calls and configuration changes
- Find who made specific changes
- Correlate changes with issue timelines
- Audit security events

### EC2 and Networking
- Describe instances, security groups, VPCs
- Check network ACLs and route tables
- Investigate connectivity issues
- Review instance metadata and status

### RDS Database Issues
- Check database instance status and configuration
- Review security groups and network access
- Analyze performance metrics
- Look up recent events and modifications

### EKS/Container Issues
- Describe cluster configuration
- Check node group status
- Query CloudWatch Container Insights
- Review pod logs and metrics

### Load Balancers
- Check target health
- Review listener configurations
- Investigate traffic patterns
- Analyze access logs

### Cost and Usage
- Query cost and usage reports
- Analyze spending trends
- Identify expensive resources

## Example Usage

### Database Connection Issues
```
"My application can't connect to RDS after 3 PM yesterday"
```

### Cost Spike Investigation
```
"Our AWS costs increased 40% last week"
```

### Check IAM Policy for a k8s workload
```
"What IAM policy is the aws mcp using? What capabilities does it have?"
```
