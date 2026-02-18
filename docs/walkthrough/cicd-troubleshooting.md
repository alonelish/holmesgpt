# CI/CD Pipeline Troubleshooting

HolmesGPT can be integrated into CI/CD pipelines to automatically troubleshoot failures. When a pipeline step fails, Holmes kicks in to investigate and provide root cause analysis.

There are two ways to integrate Holmes into your CI/CD pipeline:

- **[Use your existing Holmes deployment](#use-your-existing-holmes-deployment)** (recommended) - Call the Holmes HTTP API from your pipeline. This uses your already-curated Holmes instance with full cluster visibility, custom runbooks, and access to all configured data sources.
- **[Install Holmes in the CI runner](#install-holmes-in-the-ci-runner)** - Install HolmesGPT directly in the runner. Simpler to set up but limited to what the runner can access.

!!! tip "Which approach should I use?"

    If you already have Holmes deployed in your cluster (via Helm or Robusta), use the API approach. It can diagnose issues that aren't visible from inside the CI runner — like nodes running out of memory, pods being evicted, or jobs taking longer than expected to schedule.

![CI/CD Failure Example](../assets/cicd-failure-example.png)

## Use Your Existing Holmes Deployment

Call the [Holmes HTTP API](../reference/http-api.md) from your GitHub Actions workflow. This lets your pipeline leverage the same Holmes instance you've already configured with your runbooks, toolsets, and data sources.

### Prerequisites

- Holmes deployed in your cluster with an accessible API endpoint (see [Helm Chart installation](../installation/kubernetes-installation.md))
- The Holmes API must be reachable from your CI runner (see [Exposing the API](#exposing-the-api) below)

### Exposing the API

Your CI runner needs network access to the Holmes API. Choose the method that fits your setup:

=== "Ingress / Load Balancer"

    Expose Holmes via an Ingress or LoadBalancer Service. This is the most common approach for production use.

    ```yaml
    # Example: LoadBalancer Service
    apiVersion: v1
    kind: Service
    metadata:
      name: holmes-external
    spec:
      type: LoadBalancer
      selector:
        app: holmes
      ports:
        - port: 80
          targetPort: 8080
    ```

    !!! warning
        Secure the endpoint with authentication, network policies, or IP allowlisting. The Holmes API does not have built-in authentication.

=== "Self-Hosted Runner"

    If your GitHub Actions runner is self-hosted inside the same cluster or VPC, it can reach the Holmes ClusterIP service directly:

    ```
    http://holmesgpt-holmes.default.svc.cluster.local:80
    ```

    Replace `default` with the namespace where Holmes is installed.

### GitHub Actions Workflow

Add an on-failure step that calls the Holmes API to investigate what went wrong:

```yaml
name: Deploy to Production

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Deploy application
        run: |
          # Your deployment steps here
          kubectl apply -f k8s/
          kubectl rollout status deployment/my-app --timeout=300s

      - name: Investigate failure with Holmes
        if: failure()
        env:
          HOLMES_URL: ${{ secrets.HOLMES_URL }}
        run: |
          RESPONSE=$(curl -s -X POST "$HOLMES_URL/api/chat" \
            -H "Content-Type: application/json" \
            -d "{
              \"ask\": \"The deployment of my-app failed in the CI/CD pipeline for repo ${{ github.repository }}, commit ${{ github.sha }}. Investigate why the pods are not becoming ready.\",
              \"model\": \"claude-sonnet\"
            }")

          ANALYSIS=$(echo "$RESPONSE" | jq -r '.analysis // "No analysis returned"')

          echo "## Holmes Investigation" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "$ANALYSIS" >> $GITHUB_STEP_SUMMARY
```

This workflow:

1. Runs your normal deployment
2. If any previous step fails, calls the Holmes API to investigate
3. Writes the analysis to the [GitHub Actions job summary](https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/workflow-commands-for-github-actions#adding-a-job-summary) so you can read it directly in the workflow run

### Sending Results to Slack

To also send the investigation results to Slack, use the `/api/chat` endpoint with a prompt that includes the Slack context, or make a second call after getting the analysis:

```yaml
      - name: Investigate failure with Holmes
        if: failure()
        env:
          HOLMES_URL: ${{ secrets.HOLMES_URL }}
          SLACK_WEBHOOK: ${{ secrets.SLACK_WEBHOOK }}
        run: |
          RESPONSE=$(curl -s -X POST "$HOLMES_URL/api/chat" \
            -H "Content-Type: application/json" \
            -d "{
              \"ask\": \"The deployment of my-app failed in repo ${{ github.repository }}, commit ${{ github.sha }}. Run: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}. Investigate why the pods are not becoming ready.\",
              \"model\": \"claude-sonnet\"
            }")

          ANALYSIS=$(echo "$RESPONSE" | jq -r '.analysis // "No analysis returned"')

          # Write to job summary
          echo "## Holmes Investigation" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "$ANALYSIS" >> $GITHUB_STEP_SUMMARY

          # Send to Slack
          curl -s -X POST "$SLACK_WEBHOOK" \
            -H "Content-Type: application/json" \
            -d "{\"text\": \"*Holmes Investigation — ${{ github.repository }}*\n\n$ANALYSIS\"}"
```

### Proactive Analysis

Holmes doesn't have to wait for failures. You can add a step that runs on every deployment to proactively check for potential issues:

```yaml
      - name: Post-deploy health check with Holmes
        if: success()
        env:
          HOLMES_URL: ${{ secrets.HOLMES_URL }}
        run: |
          RESPONSE=$(curl -s -X POST "$HOLMES_URL/api/chat" \
            -H "Content-Type: application/json" \
            -d "{
              \"ask\": \"I just deployed my-app in the production namespace. Check if the new pods are healthy, look for any warning signs like high restart counts, resource pressure, or pending pods.\",
              \"model\": \"claude-sonnet\"
            }")

          ANALYSIS=$(echo "$RESPONSE" | jq -r '.analysis // "No analysis returned"')

          echo "## Post-Deploy Health Check" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "$ANALYSIS" >> $GITHUB_STEP_SUMMARY
```

## Install Holmes in the CI Runner

Install HolmesGPT directly in the CI runner to investigate failures. This approach is simpler to set up but the runner only has access to what's available in its environment (e.g., kubectl access if configured).

```yaml
name: Deploy to Production

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install HolmesGPT
        run: pip install holmesgpt

      - name: Deploy application
        run: |
          kubectl apply -f k8s/
          if ! kubectl rollout status deployment/my-app --timeout=300s; then
            echo "DEPLOY_FAILED=true" >> $GITHUB_ENV
          fi

      - name: Investigate failure with Holmes
        if: env.DEPLOY_FAILED == 'true'
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          holmes ask \
            "The deployment of my-app failed in ${{ github.repository }}.
            Commit: ${{ github.sha }}
            Pipeline: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
            Investigate why the pods are not becoming ready." \
            --model anthropic/claude-sonnet-4-5-20250929 \
            --no-interactive
          exit 1
```

### With Slack Notifications

```yaml
      - name: Investigate failure with Holmes
        if: env.DEPLOY_FAILED == 'true'
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          holmes ask \
            "The deployment of my-app failed in ${{ github.repository }}.
            Commit: ${{ github.sha }}
            Pipeline: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
            Investigate why the pods are not becoming ready." \
            --model anthropic/claude-sonnet-4-5-20250929 \
            --no-interactive \
            --destination slack \
            --slack-token "${{ secrets.SLACK_TOKEN }}" \
            --slack-channel "#deploy-alerts"
          exit 1
```

### Simplified Version

For basic deployments, a one-liner in your existing script:

```bash
kubectl rollout status deployment/my-app -n prod --timeout=300s || \
  holmes ask "deployment/my-app in prod namespace failed to roll out" \
    --model anthropic/claude-sonnet-4-5-20250929 \
    --no-interactive \
    --destination slack \
    --slack-token "$SLACK_TOKEN" \
    --slack-channel "#alerts"
```

## Common Use Cases

```
The deployment failed and pods are in CrashLoopBackOff. What is the root cause?
```

```
The CI pipeline deployment is timing out. Check if there are resource constraints or scheduling issues on the nodes.
```

```
I just deployed my-app. Are the new pods healthy? Check for high memory usage, restart counts, or any warning events.
```

```
The deployment rollout is stuck. Check if there are image pull errors, node pressure, or quota issues.
```
