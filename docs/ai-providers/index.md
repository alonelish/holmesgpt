# AI Providers

HolmesGPT supports multiple AI providers, giving you flexibility in choosing the best model for your needs and budget.

<div class="grid cards" markdown>

-   [:simple-anthropic:{ .lg .middle } **Anthropic**](anthropic.md)
-   [:material-aws:{ .lg .middle } **AWS Bedrock**](aws-bedrock.md)
-   [:material-microsoft-azure:{ .lg .middle } **Azure OpenAI**](azure-openai.md)
-   [:simple-googlegemini:{ .lg .middle } **Gemini**](gemini.md)
-   [:material-google-cloud:{ .lg .middle } **Google Vertex AI**](google-vertex-ai.md)
-   [:simple-ollama:{ .lg .middle } **Ollama**](ollama.md)
-   [:simple-openai:{ .lg .middle } **OpenAI**](openai.md)
-   [:material-api:{ .lg .middle } **OpenAI-Compatible**](openai-compatible.md)
-   [:material-earth:{ .lg .middle } **OpenRouter**](openrouter.md)
-   [:material-robot:{ .lg .middle } **Robusta AI**](robusta-ai.md)
-   [:material-layers-triple:{ .lg .middle } **Using Multiple Providers**](using-multiple-providers.md)

</div>

## Quick Start

!!! tip "Recommended for New Users"
    **OpenAI models** provide a good balance of accuracy and speed.

    **Anthropic models** often give better results at the expense of speed.

    To get started with an OpenAI model:

    1. Get an [OpenAI API key](https://platform.openai.com/api-keys){:target="_blank"}
    2. Set `export OPENAI_API_KEY="your-api-key"`
    3. Run `holmes ask "what pods are failing?"` (OpenAI is the default provider)

Choose your provider above to see detailed configuration instructions.

## LLM Setup Overview

Setting up an LLM provider with HolmesGPT involves two main steps:

### Step 1: Get API Credentials

Obtain API keys or credentials from your chosen provider:

| Provider | Get Credentials |
|----------|-----------------|
| OpenAI | [OpenAI API Keys](https://platform.openai.com/api-keys){:target="_blank"} |
| Anthropic | [Anthropic Console](https://console.anthropic.com/){:target="_blank"} |
| Azure OpenAI | [Azure Portal](https://portal.azure.com/){:target="_blank"} - Create an OpenAI resource |
| AWS Bedrock | [AWS Console](https://console.aws.amazon.com/bedrock/){:target="_blank"} - Enable model access |
| Google Vertex AI | [GCP Console](https://console.cloud.google.com/vertex-ai){:target="_blank"} - Enable Vertex AI API |
| Ollama | No credentials needed - [self-hosted](https://ollama.com/){:target="_blank"} |

### Step 2: Configure HolmesGPT

=== "CLI"

    Set environment variables for your provider:

    ```bash
    # OpenAI (default)
    export OPENAI_API_KEY="sk-..."
    holmes ask "what pods are failing?"

    # Anthropic
    export ANTHROPIC_API_KEY="sk-ant-..."
    holmes ask "what pods are failing?" --model "anthropic/claude-sonnet-4-20250514"

    # Azure OpenAI
    export AZURE_API_KEY="..."
    export AZURE_API_BASE="https://your-resource.openai.azure.com/"
    export AZURE_API_VERSION="2024-02-15-preview"
    holmes ask "what pods are failing?" --model "azure/your-deployment-name"
    ```

=== "Kubernetes (Helm)"

    Configure the `modelList` in your Helm values:

    ```yaml
    # values.yaml
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
    ```

    See [Kubernetes Installation](../installation/kubernetes-installation.md) for complete setup instructions.

=== "Docker"

    Pass credentials as environment variables:

    ```bash
    # OpenAI
    docker run -it --rm \
      -e OPENAI_API_KEY="sk-..." \
      us-central1-docker.pkg.dev/genuine-flight-317411/devel/holmes \
      ask "what is 2+2?"

    # Anthropic
    docker run -it --rm \
      -e ANTHROPIC_API_KEY="sk-ant-..." \
      us-central1-docker.pkg.dev/genuine-flight-317411/devel/holmes \
      ask "what is 2+2?" --model "anthropic/claude-sonnet-4-20250514"
    ```

## Configuration

Each AI provider requires specific environment variables for authentication. See the [Environment Variables Reference](../reference/environment-variables.md) for a complete list of all configuration options beyond just API keys.
