# Prometheus

Connect HolmesGPT to Prometheus for metrics analysis and PromQL query generation.

**Jump to:** [Standard Prometheus](#configuration) | [Coralogix](#coralogix) | [AWS AMP](#aws-managed-prometheus-amp) | [Azure](#azure-managed-prometheus) | [Google Managed](#google-managed-prometheus) | [Grafana Cloud](#grafana-cloud-mimir)

---

## Configuration

```yaml
toolsets:
  prometheus/metrics:
    enabled: true
    config:
      prometheus_url: http://<your-prometheus-service>:9090
      # headers:
      #   Authorization: "Basic <base64_encoded_credentials>"
      # discover_metrics_from_last_hours: 1      # Metric discovery lookback window (default: 1)
      # query_timeout_seconds_default: 20        # Default query timeout (default: 20)
      # query_timeout_seconds_hard_max: 180      # Max allowed query timeout (default: 180)
      # metadata_timeout_seconds_default: 20     # Metadata API timeout (default: 20)
      # metadata_timeout_seconds_hard_max: 60    # Max metadata API timeout (default: 60)
      # rules_cache_duration_seconds: 1800       # Rules cache TTL (default: 1800, null to disable)
      # verify_ssl: true                         # SSL verification (default: true)
      # tool_calls_return_data: true             # Return query data (default: true)
      # query_response_size_limit_pct: null      # Token limit as % of context window
```

**Environment variables** (alternative to config file):

- `PROMETHEUS_URL`: Prometheus server URL
- `PROMETHEUS_AUTH_HEADER`: Authorization header value (e.g., `Bearer token123`)

## Validation

```bash
holmes ask "Show me CPU usage for the last hour"
```

## Finding Your Prometheus URL

??? note "Need help finding your Prometheus URL?"

    **Ask Holmes to help you find it:**
    ```
    I need to configure HolmesGPT to connect to Prometheus. Can you help me:
    1. List all Prometheus-related services in my Kubernetes cluster
    2. Determine which one is the main Prometheus server
    3. Provide the full service URL I should use

    Run: kubectl get svc -A | grep -i prom
    ```

    **Quick methods:**

    **Port-forward for testing:**
    ```bash
    kubectl get svc -A | grep prometheus
    kubectl port-forward svc/<prometheus-service> 9090:9090 -n <namespace>
    # Access at: http://localhost:9090
    ```

    **Get internal cluster URL:**
    ```bash
    kubectl get svc -A -o jsonpath='{range .items[*]}{.metadata.name}.{.metadata.namespace}.svc.cluster.local:{.spec.ports[0].port}{"\n"}{end}' | grep prometheus
    ```

## Capabilities

| Tool | Description |
|------|-------------|
| `list_prometheus_rules` | List Prometheus rules with descriptions and annotations |
| `get_metric_names` | Get metric names (fastest discovery method) |
| `get_label_values` | Get values for a label (pods, namespaces, jobs) |
| `get_all_labels` | Get all available label names |
| `get_series` | Get time series with full label sets |
| `get_metric_metadata` | Get metric type, description, and unit |
| `execute_prometheus_instant_query` | Execute instant PromQL query |
| `execute_prometheus_range_query` | Execute range PromQL query with graph |

---

## Coralogix

```yaml
toolsets:
  prometheus/metrics:
    enabled: true
    config:
      prometheus_url: "https://prom-api.<region>.coralogix.com"  # See regions below
      headers:
        token: "{{ env.CORALOGIX_API_KEY }}"
      discover_metrics_from_last_hours: 72
      # query_timeout_seconds_default: 20
      # query_timeout_seconds_hard_max: 180
```

**Setup:**

1. Find your [regional PromQL endpoint](https://coralogix.com/docs/integrations/coralogix-endpoints/#promql)
2. Create API key in Coralogix (Data Flow → API Keys) with metrics query permissions
3. Store key in Kubernetes secret and reference via `{{ env.CORALOGIX_API_KEY }}`

---

## AWS Managed Prometheus (AMP)

```yaml
toolsets:
  prometheus/metrics:
    enabled: true
    config:
      prometheus_url: https://aps-workspaces.<region>.amazonaws.com/workspaces/<workspace-id>/
      aws_region: us-east-1
      # aws_service_name: aps                    # Default: aps
      # aws_access_key: "{{ env.AWS_ACCESS_KEY_ID }}"
      # aws_secret_access_key: "{{ env.AWS_SECRET_ACCESS_KEY }}"
      # assume_role_arn: "arn:aws:iam::123456789012:role/PrometheusReadRole"
      # refresh_interval_seconds: 900            # AWS credential refresh (default: 900)
      # verify_ssl: false                        # Default: false for AMP
      # additional_labels:                       # Labels added to all queries
      #   cluster: "production"
```

**Notes:**

- Automatically uses SigV4 authentication when `aws_region` is present
- Uses default AWS credential chain if keys not specified
- Supports cross-account access via `assume_role_arn`

---

## Azure Managed Prometheus

```yaml
toolsets:
  prometheus/metrics:
    enabled: true
    config:
      prometheus_url: "https://<your-workspace>.<region>.prometheus.monitor.azure.com:443/"
      # azure_client_id: "{{ env.AZURE_CLIENT_ID }}"
      # azure_tenant_id: "{{ env.AZURE_TENANT_ID }}"
      # azure_client_secret: "{{ env.AZURE_CLIENT_SECRET }}"
      # azure_use_managed_id: false              # Use managed identity instead of service principal
      # refresh_interval_seconds: 900            # Token refresh interval (default: 900)
      # verify_ssl: true                         # SSL verification (default: true)
```

**Environment variables** (alternative to config):

- `AZURE_CLIENT_ID`: Service principal client ID
- `AZURE_TENANT_ID`: Azure AD tenant ID
- `AZURE_CLIENT_SECRET`: Service principal secret
- `AZURE_USE_MANAGED_ID`: Set to `true` for managed identity auth

**Notes:**

- Authentication is handled automatically via Azure AD
- Some tools unavailable: `get_label_values`, `get_metric_metadata`, `list_prometheus_rules`
- Include trailing slash in `prometheus_url`

---

## Google Managed Prometheus

```yaml
toolsets:
  prometheus/metrics:
    enabled: true
    config:
      prometheus_url: http://frontend.<namespace>.svc.cluster.local:9090
```

**Prerequisites:**

- Google Managed Prometheus enabled
- [Prometheus Frontend](https://cloud.google.com/stackdriver/docs/managed-prometheus/query-api-ui#ui-prometheus) deployed and accessible

Authentication is automatic via Workload Identity or default service account.

---

## Grafana Cloud (Mimir)

```yaml
toolsets:
  prometheus/metrics:
    enabled: true
    config:
      prometheus_url: https://<instance>.grafana.net/api/datasources/proxy/uid/<datasource-uid>
      headers:
        Authorization: "Bearer <glsa_token>"
```

**Setup:**

1. Create service account token in Grafana Cloud (Administration → Service accounts)
2. Find Prometheus datasource UID:
   ```bash
   curl -H "Authorization: Bearer <glsa_token>" \
        "https://<instance>.grafana.net/api/datasources" | \
        jq '.[] | select(.type=="prometheus") | .uid'
   ```
3. Use the proxy endpoint format: `/api/datasources/proxy/uid/<uid>`

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Connection refused | Verify URL is accessible from HolmesGPT |
| Authentication errors | Check `headers` configuration |
| SSL certificate errors | Set `verify_ssl: false` (not recommended for production) |
| No metrics returned | Ensure Prometheus is scraping targets |
| Query timeouts | Increase `query_timeout_seconds_default` |
