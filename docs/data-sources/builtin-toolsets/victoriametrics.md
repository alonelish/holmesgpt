# VictoriaMetrics

Connect HolmesGPT to VictoriaMetrics, a Prometheus-compatible time-series database, for metric queries during investigations.

## When to Use This

- ✅ You run VictoriaMetrics as a Prometheus replacement or long-term store
- ✅ You want lighter resource usage than a full kube-prometheus-stack
- ✅ You have `vmsingle` or `vmcluster` running in your cluster

## Prerequisites

- A running VictoriaMetrics instance (`vmsingle`, `vmselect`, or `vmauth`) reachable from where Holmes runs
- The HTTP API endpoint (typically port 8428 for `vmsingle`, 8481 for `vmselect`)

## Configuration

HolmesGPT uses its built-in Prometheus toolset to query VictoriaMetrics — VM's HTTP API implements the Prometheus query API, so no separate toolset is required.

```yaml-toolset-config
toolsets:
  prometheus/metrics:
    enabled: true
    config:
      prometheus_url: "http://vmsingle-vmsingle.monitoring.svc.cluster.local:8428"
```

For the complete list of supported configuration fields (authentication headers, timeouts, label filters, SSL verification, etc.), see the [Prometheus toolset configuration reference](prometheus.md#configuration).

## Quick Install with Helm

If you don't already have VictoriaMetrics running in your cluster:

```bash
helm repo add vm https://victoriametrics.github.io/helm-charts/
helm repo update

helm upgrade --install vmsingle vm/victoria-metrics-single \
  --namespace monitoring --create-namespace
```

This deploys a standalone `vmsingle` pod (~200–300MB RAM). The service is reachable at `http://vmsingle-vmsingle.monitoring.svc.cluster.local:8428`.

For production deployments with scraping, long-term storage, and alerting, use the heavier `victoria-metrics-k8s-stack` chart instead — see the [VictoriaMetrics Helm charts documentation](https://docs.victoriametrics.com/helm/).

## Compatibility Notes

VictoriaMetrics implements the Prometheus query API, but a few less-common endpoints that Holmes uses have partial or no support:

| Holmes feature | VM support |
|---|---|
| PromQL instant and range queries | Full |
| Label discovery (`/api/v1/labels`, `/api/v1/label/<name>/values`) | Full |
| Series queries (`/api/v1/series`) | Full |
| Metric metadata (`/api/v1/metadata`) | Partial — fewer descriptions than Prometheus |
| Alert / recording rules (`/api/v1/rules`) | Requires `vmalert`; absent in `vmsingle` |

For day-to-day metric investigations, Holmes works the same as with Prometheus. Advanced metric discovery (via the metadata API) and alert-rule listing (via `vmalert`) may be reduced or unavailable.

## Capabilities

Inherits every tool from the Prometheus toolset. See the [Prometheus capabilities](prometheus.md#capabilities) reference for the full list of tools Holmes can use against a VictoriaMetrics endpoint.
