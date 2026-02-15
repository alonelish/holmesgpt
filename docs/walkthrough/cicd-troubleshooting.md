# CI/CD Pipeline Troubleshooting

HolmesGPT can be integrated into CI/CD pipelines to automatically troubleshoot deployment failures, providing instant root cause analysis when rollouts fail. Results can be posted as PR comments, sent to Slack, or saved to files for downstream processing.

![CI/CD Failure Example](../assets/cicd-failure-example.png)

## Quick Start

The core pattern is: detect a deployment failure, then run `holmes ask` with context about what failed.

```bash
pip install holmesgpt

kubectl rollout status deployment/my-app -n prod --timeout=300s || \
  holmes ask "The deployment of my-app in the prod namespace failed. \
    Investigate the root cause and focus only on unhealthy components." \
    --model anthropic/claude-sonnet-4-5-20250929 \
    --no-interactive
```

## GitHub Actions

=== "PR Comment on Failure"

    Post the investigation as a PR comment so the team gets immediate context on what went wrong.

    ```yaml
    name: Deploy and Analyze Failures

    on:
      pull_request:
        branches: [main]

    jobs:
      deploy:
        runs-on: ubuntu-latest
        steps:
        - uses: actions/checkout@v4

        # ... your deployment steps (kubectl apply, helm upgrade, etc.) ...

        - name: Wait for rollout
          id: rollout
          continue-on-error: true
          run: |
            kubectl rollout status deployment/${{ env.APP_NAME }} \
              -n ${{ env.NAMESPACE }} --timeout=300s

        - name: Run HolmesGPT investigation
          if: steps.rollout.outcome == 'failure'
          env:
            ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          run: |
            pip install holmesgpt
            holmes ask \
              "Please investigate the root cause for the deployment failure \
              of the ${{ env.APP_NAME }} rollout within the ${{ env.NAMESPACE }} namespace. \
              In your report, include only what you believe is the issue, \
              and disregard the healthy components. \
              Additionally, provide a short, 3-6 words title to the issue, \
              in the format of ### TL;DR: <YOUR_TITLE_HERE> ###" \
              --model anthropic/claude-sonnet-4-5-20250929 \
              --no-interactive > /tmp/holmes-output.txt

        - name: Comment on PR
          if: steps.rollout.outcome == 'failure'
          uses: actions/github-script@v7
          with:
            script: |
              const fs = require('fs');
              const output = fs.readFileSync('/tmp/holmes-output.txt', 'utf8');
              await github.rest.issues.createComment({
                owner: context.repo.owner,
                repo: context.repo.repo,
                issue_number: context.issue.number,
                body: `## HolmesGPT Deployment Analysis\n\n${output}`
              });

        - name: Fail if deployment failed
          if: steps.rollout.outcome == 'failure'
          run: exit 1
    ```

=== "Slack Notification"

    Send the investigation directly to a Slack channel.

    ```yaml
    name: Deploy to Production

    on:
      push:
        branches: [main]

    jobs:
      deploy:
        runs-on: ubuntu-latest
        steps:
        - uses: actions/checkout@v4

        # ... your deployment steps ...

        - name: Deploy and investigate failures
          env:
            ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          run: |
            # Deploy
            kubectl apply -f k8s/
            # Wait for rollout, investigate on failure
            if ! kubectl rollout status deployment/${{ env.APP_NAME }} \
              -n ${{ env.NAMESPACE }} --timeout=300s; then
              pip install holmesgpt
              holmes ask \
                "Deployment of ${{ env.APP_NAME }} failed in ${{ env.NAMESPACE }}. \
                Commit: ${{ github.sha }}. \
                Pipeline: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}. \
                Investigate why pods are not becoming ready." \
                --model anthropic/claude-sonnet-4-5-20250929 \
                --no-interactive \
                --destination slack \
                --slack-token "${{ secrets.SLACK_TOKEN }}" \
                --slack-channel "#deploy-alerts"
              exit 1
            fi
    ```

=== "Reusable Action"

    Create a reusable composite action so any workflow in your organization can call HolmesGPT with a single step.

    **`.github/actions/holmes-investigate/action.yml`:**

    ```yaml
    name: HolmesGPT Investigation
    description: Run HolmesGPT to investigate a deployment failure

    inputs:
      release-name:
        description: Name of the Helm release or deployment
        required: true
      namespace:
        description: Kubernetes namespace
        required: true
      anthropic-api-key:
        description: Anthropic API key
        required: true
      model:
        description: LLM model to use
        required: false
        default: anthropic/claude-sonnet-4-5-20250929

    outputs:
      analysis:
        description: Path to the analysis output file
        value: ${{ steps.investigate.outputs.output-file }}

    runs:
      using: composite
      steps:
        - name: Install HolmesGPT
          shell: bash
          run: pip install holmesgpt

        - name: Investigate failure
          id: investigate
          shell: bash
          env:
            ANTHROPIC_API_KEY: ${{ inputs.anthropic-api-key }}
          run: |
            OUTPUT_FILE="/tmp/holmes-analysis-${{ github.run_id }}.txt"
            holmes ask \
              "Please investigate the root cause for the deployment failure \
              of the ${{ inputs.release-name }} rollout within the \
              ${{ inputs.namespace }} namespace. \
              In your report, include only what you believe is the issue, \
              and disregard the healthy components. \
              Additionally, provide a short, 3-6 words title to the issue, \
              in the format of ### TL;DR: <YOUR_TITLE_HERE> ###" \
              --model ${{ inputs.model }} \
              --no-interactive > "$OUTPUT_FILE"
            echo "output-file=$OUTPUT_FILE" >> "$GITHUB_OUTPUT"
    ```

    **Using it in a deployment workflow:**

    ```yaml
    - name: Investigate deployment failure
      if: steps.deploy.outcome == 'failure'
      uses: ./.github/actions/holmes-investigate
      id: holmes
      with:
        release-name: my-app
        namespace: production
        anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}

    - name: Comment on PR
      if: steps.deploy.outcome == 'failure'
      uses: actions/github-script@v7
      with:
        script: |
          const fs = require('fs');
          const output = fs.readFileSync('${{ steps.holmes.outputs.analysis }}', 'utf8');
          await github.rest.issues.createComment({
            owner: context.repo.owner,
            repo: context.repo.repo,
            issue_number: context.issue.number,
            body: `## Deployment Failure Analysis\n\n${output}`
          });
    ```

=== "Docker (No Python Required)"

    Use the HolmesGPT Docker image if you don't want to install Python in your CI environment.

    ```yaml
    - name: Investigate failure
      if: steps.rollout.outcome == 'failure'
      run: |
        docker run --rm \
          --network host \
          -e ANTHROPIC_API_KEY="${{ secrets.ANTHROPIC_API_KEY }}" \
          -v $HOME/.kube/config:/root/.kube/config \
          us-central1-docker.pkg.dev/genuine-flight-317411/devel/holmes \
          ask "Deployment of ${{ env.APP_NAME }} failed in ${{ env.NAMESPACE }}. \
            Investigate why pods are not becoming ready." \
          --model anthropic/claude-sonnet-4-5-20250929 \
          --no-interactive > /tmp/holmes-output.txt
    ```

## GitLab CI

```yaml
deploy:
  stage: deploy
  script:
    # ... your deployment commands ...
    - kubectl apply -f k8s/
    - |
      if ! kubectl rollout status deployment/$APP_NAME -n $NAMESPACE --timeout=300s; then
        pip install holmesgpt
        holmes ask \
          "Deployment of $APP_NAME failed in $NAMESPACE. \
          Pipeline: $CI_PIPELINE_URL. Commit: $CI_COMMIT_SHA. \
          Investigate why pods are not becoming ready." \
          --model anthropic/claude-sonnet-4-5-20250929 \
          --no-interactive \
          --destination slack \
          --slack-token "$SLACK_TOKEN" \
          --slack-channel "#deploy-alerts"
        exit 1
      fi
```

## Tips for Writing Effective Prompts

A well-crafted prompt makes a significant difference in CI/CD investigations. Here are patterns that work well in production:

**Focus on unhealthy components only** -- deployment environments have many healthy services. Ask Holmes to ignore them:

```
In your report, include only what you believe is the issue,
and disregard the healthy components.
```

**Request structured output** -- ask for a TL;DR title so you can quickly scan PR comments:

```
Additionally, provide a short, 3-6 words title to the issue,
in the format of ### TL;DR: <YOUR_TITLE_HERE> ###
```

**Include deployment context** -- give Holmes the release name, namespace, commit SHA, and pipeline URL so it can correlate the failure with the change:

```
Deployment of payment-service failed in the production namespace.
Commit: abc123. Pipeline: https://github.com/org/repo/actions/runs/12345.
```

## Saving Output for Downstream Steps

Use `--json-output-file` to save structured JSON output for programmatic processing:

```bash
holmes ask "investigate the deployment failure" \
  --model anthropic/claude-sonnet-4-5-20250929 \
  --no-interactive \
  --json-output-file /tmp/holmes-result.json
```

Or redirect stdout to capture the text analysis:

```bash
holmes ask "investigate the deployment failure" \
  --model anthropic/claude-sonnet-4-5-20250929 \
  --no-interactive > /tmp/holmes-output.txt
```

## Common Use Cases

```bash
# Investigate a failed Helm upgrade
holmes ask "The helm upgrade of payment-service in production failed. Investigate the root cause." \
  --model anthropic/claude-sonnet-4-5-20250929 --no-interactive
```

```bash
# Analyze post-deploy health check failure
holmes ask "After deploying commit abc123, the health check for api-gateway \
  in the staging namespace is failing. What changed?" \
  --model anthropic/claude-sonnet-4-5-20250929 --no-interactive
```

```bash
# Investigate a canary deployment regression
holmes ask "The canary deployment of checkout-service in prod shows increased \
  error rates. Investigate what's different between the canary and stable pods." \
  --model anthropic/claude-sonnet-4-5-20250929 --no-interactive
```
