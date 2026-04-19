# Loki

Connect HolmesGPT to Loki for log analysis through Grafana or direct API access. Provides access to historical logs and advanced log queries.

## When to Use This

- ✅ Your Kubernetes logs are centralized in Loki
- ✅ You need historical log data beyond what's in pods
- ✅ You want advanced log search capabilities

## Prerequisites

- Loki instance with logs from your Kubernetes cluster
- Grafana with Loki datasource configured (recommended) OR direct Loki API access

--8<-- "snippets/toolsets_that_provide_logging.md"

## Configuration

HolmesGPT supports two ways to connect to Loki. Pick the one that matches your setup:

| Setup | When to use |
|-------|-------------|
| [Loki via Grafana](#loki-via-grafana-recommended) (recommended) | You already have Grafana with a Loki datasource configured (works for self-hosted Grafana and Grafana Cloud) |
| [Direct Loki](#direct-loki) | Self-hosted Loki without Grafana, including multi-tenant setups needing `X-Scope-OrgID` |

### Loki via Grafana (Recommended)

HolmesGPT queries Loki through your Grafana instance's datasource proxy. Recommended when you already have Grafana — it handles authentication and you only need one API key. This is also the only mode that produces clickable Grafana Explore links in Holmes's responses.

**Required:**

- [Grafana service account token](https://grafana.com/docs/grafana/latest/administration/service-accounts/) with Viewer role
- Loki datasource UID from Grafana

**Find your Loki datasource UID:**

```bash
# Port forward to Grafana
kubectl port-forward svc/grafana 3000:80

# Get Loki datasource UID
curl -s -u admin:admin http://localhost:3000/api/datasources | jq '.[] | select(.type == "loki") | .uid'
```

```yaml-toolset-config
toolsets:
  grafana/loki:
    enabled: true
    config:
      api_url: https://xxxxxxx.grafana.net  # Your Grafana URL
      api_key: <your grafana API key>
      grafana_datasource_uid: <the UID of the loki data source in Grafana>
```

### Direct Loki

HolmesGPT connects directly to a self-hosted Loki API endpoint without going through Grafana.

```yaml-toolset-config
toolsets:
  grafana/loki:
    enabled: true
    config:
      api_url: http://loki.logging:3100
      additional_headers:
        X-Scope-OrgID: "<tenant id>"  # Only needed for multi-tenant Loki
```

## Advanced Configuration

### SSL Verification

For self-signed certificates, you can disable SSL verification:

```yaml-toolset-config
toolsets:
  grafana/loki:
    enabled: true
    config:
      api_url: https://loki.internal
      verify_ssl: false  # Disable SSL verification (default: true)
```

### External URL

If HolmesGPT accesses Loki through an internal URL but you want clickable links in results to use a different URL:

```yaml-toolset-config
toolsets:
  grafana/loki:
    enabled: true
    config:
      api_url: http://loki.internal:3100  # Internal URL for API calls
      external_url: https://loki.example.com  # URL for links in results
```

## Capabilities

| Tool Name | Description |
|-----------|-------------|
| fetch_pod_logs | Fetches pod logs from Loki |
