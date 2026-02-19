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

Choose one of the following methods:

### Option 1: Through Grafana (Recommended)

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

**Configuration (Grafana Proxy):**

```yaml-toolset-config
toolsets:
  grafana/loki:
    enabled: true
    config:
      api_key: <your grafana service account token>
      api_url: http://grafana.monitoring.svc:3000
      grafana_datasource_uid: <the UID of the loki data source in Grafana>

  kubernetes/logs:
    enabled: false # HolmesGPT's default logging mechanism MUST be disabled
```

### Option 2: Grafana Cloud

**1. Create a service account token in Grafana Cloud:**

   - Navigate to "Administration → Service accounts"
   - Create a new service account with "Viewer" role
   - Generate a service account token (starts with `glsa_`)

**2. Find your Loki datasource UID:**

```bash
curl -H "Authorization: Bearer YOUR_GLSA_TOKEN" \
     "https://YOUR-INSTANCE.grafana.net/api/datasources" | \
     jq '.[] | select(.type=="loki") | {name, uid}'
```

**3. Configure HolmesGPT:**

```yaml-toolset-config
toolsets:
  grafana/loki:
    enabled: true
    config:
      api_key: <your glsa_ service account token>
      api_url: https://YOUR-INSTANCE.grafana.net
      grafana_datasource_uid: <the UID of the loki data source in Grafana>

  kubernetes/logs:
    enabled: false # HolmesGPT's default logging mechanism MUST be disabled
```

!!! warning "Getting 404 errors?"
    - **Use your Grafana instance URL** (`https://YOUR-INSTANCE.grafana.net`), not the Loki endpoint URL (`https://logs-prod-xxx.grafana.net`)
    - **Verify the datasource UID** using the `curl` command above — a wrong UID is the most common cause of 404 errors

### Option 3: Direct Connection

The toolset can directly connect to a Loki instance without proxying through a Grafana instance. This is done by not setting the `grafana_datasource_uid` field.

```yaml-toolset-config
toolsets:
  grafana/loki:
    enabled: true
    config:
      api_url: http://loki.logging
      additional_headers:
        X-Scope-OrgID: "<tenant id>" # Set the X-Scope-OrgID if loki multitenancy is enabled

  kubernetes/logs:
    enabled: false # HolmesGPT's default logging mechanism MUST be disabled
```

For **Grafana Cloud Loki direct access** (without proxying through Grafana), find your Loki URL and user ID in Grafana Cloud under "My Account → Loki":

```yaml-toolset-config
toolsets:
  grafana/loki:
    enabled: true
    config:
      api_url: https://logs-prod-XXX.grafana.net
      additional_headers:
        Authorization: "Basic <base64 of USER_ID:API_KEY>"

  kubernetes/logs:
    enabled: false # HolmesGPT's default logging mechanism MUST be disabled
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
| grafana_loki_query | Run LogQL queries against Loki |
