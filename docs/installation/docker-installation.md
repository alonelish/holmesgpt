# Install with Docker

Run HolmesGPT using prebuilt Docker images — no local Python installation needed.

## Docker Image

HolmesGPT publishes prebuilt images to Google Artifact Registry:

```
us-central1-docker.pkg.dev/genuine-flight-317411/devel/holmes
```

**Available tags:**

- `latest` — most recent release
- Version tags (e.g., `0.9.0`) — pinned to a specific release

## Quick Start

```bash
docker run -it --net=host \
  -e ANTHROPIC_API_KEY="your-api-key" \
  -v ~/.kube/config:/root/.kube/config \
  us-central1-docker.pkg.dev/genuine-flight-317411/devel/holmes \
  ask "what pods are unhealthy and why?" --model="anthropic/claude-sonnet-4-5-20250929"
```

## Volume Mounts

Mount local files so HolmesGPT can access your credentials and configuration:

```bash
docker run -it --net=host \
  -e ANTHROPIC_API_KEY="your-api-key" \
  -v ~/.holmes:/root/.holmes \
  -v ~/.kube/config:/root/.kube/config \
  -v ~/.aws:/root/.aws \
  -v ~/.config/gcloud:/root/.config/gcloud \
  us-central1-docker.pkg.dev/genuine-flight-317411/devel/holmes \
  ask "what pods are unhealthy and why?"
```

| Mount | Purpose |
|-------|---------|
| `-v ~/.kube/config:/root/.kube/config` | Kubernetes cluster access |
| `-v ~/.holmes:/root/.holmes` | HolmesGPT config file (`config.yaml`) |
| `-v ~/.aws:/root/.aws` | AWS credentials (for Bedrock or AWS toolsets) |
| `-v ~/.config/gcloud:/root/.config/gcloud` | Google Cloud credentials (for Vertex AI or GCP toolsets) |

Only mount the directories you need. For example, if you only use Kubernetes with an Anthropic API key, you only need `-v ~/.kube/config:/root/.kube/config`.

## Environment Variables

Pass API keys and configuration with `-e` flags:

```bash
# Anthropic
docker run -it --net=host -e ANTHROPIC_API_KEY="..." \
  us-central1-docker.pkg.dev/genuine-flight-317411/devel/holmes \
  ask "what pods are unhealthy?"

# OpenAI
docker run -it --net=host -e OPENAI_API_KEY="..." \
  us-central1-docker.pkg.dev/genuine-flight-317411/devel/holmes \
  ask "what pods are unhealthy?"
```

See [Environment Variables Reference](../reference/environment-variables.md) for the complete list.

## Extending the Image

If your toolsets need additional binaries, extend the base image:

```dockerfile
FROM us-central1-docker.pkg.dev/genuine-flight-317411/devel/holmes:latest

RUN apt-get update && apt-get install -y \
    your-custom-tool \
    && rm -rf /var/lib/apt/lists/*
```

See [Adding Custom Binaries](../data-sources/custom-toolsets.md#advanced-adding-custom-binaries) for details.

## Next Steps

- **[Recommended Setup](../data-sources/recommended-setup.md)** - Connect metrics, logs, and cloud providers
- **[All Data Sources](../data-sources/index.md)** - Browse 38+ built-in integrations
- **[CLI Reference](../reference/cli.md)** - All available commands and flags
