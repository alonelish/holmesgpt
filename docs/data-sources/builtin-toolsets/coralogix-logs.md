# Coralogix

HolmesGPT can use Coralogix for logs/traces (DataPrime) and, separately, PromQL-style metrics. This page shows both setups.

--8<-- "snippets/toolsets_that_provide_logging.md"

## Prerequisites
1) Coralogix API key with `DataQuerying` (logs/traces).  
2) Coralogix domain (e.g., `eu2.coralogix.com`).  
3) Team hostname (e.g., `my-team` if UI is `https://my-team.app.eu2.coralogix.com/`).

## Configuration: Coralogix (DataPrime logs/traces)
Supported fields (`CoralogixConfig`): `api_key`, `domain`, `team_hostname`, optional `labels`.

```yaml-toolset-config
toolsets:
  coralogix:
    enabled: true
    config:
      api_key: "<your Coralogix API key>"
      domain: "eu2.coralogix.com"
      team_hostname: "your-company-name"

  kubernetes/logs:
    enabled: false  # disable default Kubernetes logging if desired
```

Optional label overrides (defaults):  
- namespace=`resource.attributes.k8s.namespace.name`  
- pod=`resource.attributes.k8s.pod.name`  
- log_message=`logRecord.body`  
- timestamp=`logRecord.attributes.time`

## Configuration: Coralogix metrics (PromQL endpoint)
Metrics use a separate endpoint and token (with metrics/traces/logs permissions). Base URL: `https://ng-api-http.<your-domain>/metrics`.

```yaml
prometheus/metrics:
    enabled: true
    config:
    headers:
      Authorization: "Bearer <YOUR_METRICS_TOKEN>"
    prometheus_url: "https://ng-api-http.eu2.coralogix.com/metrics"  # replace domain
    healthcheck: "api/v1/query?query=up"
```

## Non-standard metrics/labels
If Coralogix Prometheus uses non-standard labels or custom metric names, add instructions in Holmes AI customization: go to [platform.robusta.dev](https://platform.robusta.dev/) → Settings → AI Assistant → AI Customization, add label/metric hints, and save.
