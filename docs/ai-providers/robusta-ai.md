# Robusta AI

Access multiple AI models from different providers through Robusta's unified API, without managing individual API keys.

!!! info "Robusta SaaS Feature"
    Robusta AI is available exclusively for [Robusta SaaS](https://docs.robusta.dev/master/setup-robusta/installation/index.html){:target="_blank"} customers running HolmesGPT in Kubernetes. It is not available in CLI mode.

## Prerequisites

1. An active [Robusta SaaS](https://platform.robusta.dev){:target="_blank"} account
2. HolmesGPT deployed in Kubernetes via the [Robusta Helm chart](https://docs.robusta.dev/master/setup-robusta/installation/index.html){:target="_blank"}
3. A valid [Robusta UI sink](https://docs.robusta.dev/master/configuration/sinks/RobustaUI.html){:target="_blank"} configured and operational

## Configuration

### Quick Setup

Add this to your `generated_values.yaml`:

```yaml
enableHolmesGPT: true
holmes:
  additionalEnvVars:
    - name: ROBUSTA_AI
      value: "true"
```

Then apply the changes:

--8<-- "snippets/helm_upgrade_command.md"

This deploys HolmesGPT as a server in Kubernetes and enables Robusta AI with automatic authentication through your existing Robusta UI sink token.

### Using Tokens Stored in Kubernetes Secrets

If your Robusta UI token is stored in a Kubernetes secret (common in GitOps workflows with ArgoCD or Flux), pass it explicitly via the `ROBUSTA_UI_TOKEN` environment variable:

```yaml
# Add to generated_values.yaml
holmes:
  additionalEnvVars:
    - name: ROBUSTA_UI_TOKEN
      valueFrom:
        secretKeyRef:
          name: robusta-token-secret
          key: token
    - name: ROBUSTA_AI
      value: "true"
```

!!! note
    When the Robusta UI token is defined directly in your Helm values (the default setup), HolmesGPT reads it automatically from the Robusta config file. The `ROBUSTA_UI_TOKEN` variable is only needed when the token is stored separately in a Kubernetes secret.

### Auto-Detection

When `ROBUSTA_AI` is not explicitly set, HolmesGPT auto-detects whether to enable Robusta AI. It will be enabled automatically if all of the following are true:

- HolmesGPT is running in server mode (not CLI)
- No `MODEL` environment variable is set
- No models are configured via `modelList`
- A valid Robusta UI sink token is available

To override auto-detection, set `ROBUSTA_AI` explicitly to `"true"` or `"false"`.

### Disabling Robusta AI

To use your own API keys instead of Robusta AI:

```yaml
# Add to generated_values.yaml
holmes:
  additionalEnvVars:
    - name: ROBUSTA_AI
      value: "false"
```

## Usage

When Robusta AI is enabled, available models appear in the model selector dropdown in the Robusta UI:

![Model Selection with Robusta AI](../assets/robusta-ai-model-selection-ui.png)

The specific models available depend on your Robusta subscription plan and typically include models from OpenAI and Anthropic.

## How It Works

1. HolmesGPT reads your Robusta token from the cluster configuration (or from the `ROBUSTA_UI_TOKEN` env var)
2. A session token is created with the Robusta platform
3. Available models are fetched from the Robusta API
4. All LLM requests are proxied through `https://api.robusta.dev/llm/{model_name}`
5. Authentication tokens are automatically refreshed when they expire

## Troubleshooting

```bash
# Check that ROBUSTA_AI is set
kubectl exec -n <namespace> deploy/holmes -- env | grep ROBUSTA_AI

# Check HolmesGPT logs for authentication errors
kubectl logs -n <namespace> deploy/holmes | grep -i "robusta"

# Verify network connectivity to Robusta API
kubectl exec -n <namespace> deploy/holmes -- wget -q -O- https://api.robusta.dev/api/holmes/get_info
```

## Environment Variables

| Variable | Description | Default |
|----------|------------|---------|
| `ROBUSTA_AI` | Enable or disable Robusta AI (`true`/`false`). When not set, auto-detected based on deployment context. | Auto-detected |
| `ROBUSTA_UI_TOKEN` | Base64-encoded Robusta UI token. Only needed when the token is stored in a Kubernetes secret rather than in Helm values directly. | Read from Robusta config |
| `ROBUSTA_API_ENDPOINT` | Robusta API endpoint URL. Only change this for on-premise Robusta deployments. | `https://api.robusta.dev` |
| `LOAD_ALL_ROBUSTA_MODELS` | When `true`, fetches all available models from the Robusta API. When `false`, uses only the default model. | `true` |

## See Also

- [Robusta Getting Started with HolmesGPT](https://docs.robusta.dev/master/configuration/holmesgpt/getting-started.html){:target="_blank"} - Robusta-specific setup guide
- [Robusta Platform Documentation](https://docs.robusta.dev){:target="_blank"} - Robusta setup and configuration
- [Using Multiple Providers](using-multiple-providers.md) - Configure multiple AI providers alongside Robusta AI
- [Kubernetes Installation](../installation/kubernetes-installation.md) - Deploy HolmesGPT in Kubernetes
