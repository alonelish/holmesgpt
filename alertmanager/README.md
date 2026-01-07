# AI-Alertmanager

**AI-powered Alertmanager drop-in replacement with HolmesGPT integration**

AI-Alertmanager is a 100% compatible replacement for Prometheus Alertmanager that adds AI-powered investigation capabilities. When alerts are received, it automatically investigates them using HolmesGPT and enriches alert labels with the investigation results.

## Features

- ✅ **100% Alertmanager v2 API Compatible** - Drop-in replacement for standard Alertmanager
- 🤖 **AI-Powered Investigations** - Automatic root cause analysis using HolmesGPT
- 🏷️ **Label Enrichment** - Adds investigation results as alert labels
- 🔄 **Prometheus Operator Compatible** - Works seamlessly with Prometheus Operator
- 🚀 **Background Processing** - Non-blocking investigation with configurable concurrency
- 📊 **Custom Investigation API** - Additional endpoints for querying investigation results

## Architecture

```
┌─────────────┐         ┌──────────────────┐         ┌─────────────┐
│ Prometheus  │────────▶│ AI-Alertmanager  │────────▶│ HolmesGPT   │
└─────────────┘         └──────────────────┘         └─────────────┘
                               │                              │
                               │ 1. Receive alert             │
                               │ 2. Store alert               │
                               │ 3. Enqueue investigation◀────┘
                               │ 4. Add AI labels
                               │
                               ▼
                        ┌──────────────┐
                        │ Alert with   │
                        │ ai_investigation│
                        │ label        │
                        └──────────────┘
```

## Quick Start

### Prerequisites

- Python 3.9+
- Docker (optional)
- Running HolmesGPT server
- Kubernetes cluster (for K8s deployment)

### Local Development with Docker Compose

1. **Clone the repository**
```bash
cd alertmanager
```

2. **Set up environment**
```bash
export OPENAI_API_KEY=your-api-key
```

3. **Start the services**
```bash
docker-compose up -d
```

This will start:
- HolmesGPT server on port 8080
- AI-Alertmanager on port 9093
- Prometheus on port 9090 (optional)

4. **Test the setup**
```bash
# Check health
curl http://localhost:9093/healthz

# Post a test alert
curl -X POST http://localhost:9093/api/v2/alerts \
  -H "Content-Type: application/json" \
  -d '[{
    "labels": {
      "alertname": "HighMemoryUsage",
      "severity": "warning"
    },
    "annotations": {
      "summary": "High memory usage detected"
    }
  }]'

# Check alerts (wait a few seconds for investigation)
curl http://localhost:9093/api/v2/alerts
```

### Kubernetes Deployment

#### Option 1: Standalone Deployment

```bash
# Apply the manifests
kubectl apply -f deployment.yaml

# Wait for pods to be ready
kubectl wait --for=condition=ready pod -l app=ai-alertmanager -n monitoring --timeout=300s

# Port-forward to test locally
kubectl port-forward -n monitoring svc/ai-alertmanager 9093:9093
```

#### Option 2: Replace Existing Alertmanager

If you have an existing Alertmanager deployment:

```bash
# Scale down existing Alertmanager
kubectl scale deployment alertmanager -n monitoring --replicas=0

# Deploy AI-Alertmanager
kubectl apply -f deployment.yaml

# Update Prometheus configuration to point to ai-alertmanager service
```

#### Option 3: Prometheus Operator Integration

1. **Deploy AI-Alertmanager**
```bash
kubectl apply -f deployment.yaml
```

2. **Update Prometheus CRD to use AI-Alertmanager**
```yaml
apiVersion: monitoring.coreos.com/v1
kind: Prometheus
metadata:
  name: prometheus
  namespace: monitoring
spec:
  alerting:
    alertmanagers:
    - name: ai-alertmanager
      namespace: monitoring
      port: http
```

3. **Apply the updated Prometheus configuration**
```bash
kubectl apply -f prometheus-config.yaml
```

## Configuration

All configuration is done via environment variables:

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `AI_ALERTMANAGER_HOST` | `0.0.0.0` | Server host |
| `AI_ALERTMANAGER_PORT` | `9093` | Server port |
| `AI_ALERTMANAGER_HOLMES_URL` | `http://localhost:8080` | HolmesGPT server URL |
| `AI_ALERTMANAGER_HOLMES_TIMEOUT` | `300` | HolmesGPT request timeout (seconds) |
| `AI_ALERTMANAGER_ENABLE_AI_INVESTIGATION` | `true` | Enable AI investigations |
| `AI_ALERTMANAGER_INVESTIGATE_ON_CREATE` | `true` | Auto-investigate new alerts |
| `AI_ALERTMANAGER_INVESTIGATION_CONCURRENCY` | `5` | Max concurrent investigations |
| `AI_ALERTMANAGER_INVESTIGATION_LABEL_KEY` | `ai_investigation` | Label key for AI results |
| `AI_ALERTMANAGER_INVESTIGATION_LABEL_STATUS_KEY` | `ai_investigation_status` | Label key for status |
| `AI_ALERTMANAGER_LOG_LEVEL` | `INFO` | Log level |

### Configuration Example

```yaml
# Kubernetes ConfigMap
apiVersion: v1
kind: ConfigMap
metadata:
  name: ai-alertmanager-config
data:
  AI_ALERTMANAGER_HOLMES_URL: "http://holmes.monitoring.svc.cluster.local:8080"
  AI_ALERTMANAGER_INVESTIGATION_CONCURRENCY: "10"
  AI_ALERTMANAGER_LOG_LEVEL: "DEBUG"
```

## API Reference

### Alertmanager v2 Compatible Endpoints

All standard Alertmanager v2 API endpoints are supported:

- `POST /api/v2/alerts` - Post alerts
- `GET /api/v2/alerts` - Get alerts with filters
- `GET /api/v2/alerts/groups` - Get alert groups
- `GET /api/v2/status` - Get status
- `GET /api/v2/silences` - Get silences
- `POST /api/v2/silences` - Create silence (not yet implemented)
- `DELETE /api/v2/silence/{id}` - Delete silence (not yet implemented)
- `GET /api/v2/receivers` - Get receivers

### Custom Investigation Endpoints

#### Get Investigation Result
```http
GET /api/v2/investigations/{fingerprint}
```

Returns the AI investigation result for a specific alert.

**Response:**
```json
{
  "alert_fingerprint": "abc123",
  "investigation_status": "completed",
  "analysis": "Root cause: Memory leak in application...",
  "root_cause": "Memory leak in application",
  "started_at": "2024-01-01T12:00:00Z",
  "completed_at": "2024-01-01T12:01:00Z"
}
```

#### Trigger Manual Investigation
```http
POST /api/v2/investigate/{fingerprint}
```

Manually trigger an investigation for an alert.

#### Get All Investigations
```http
GET /api/v2/investigations
```

Returns all investigation results.

## AI Label Enrichment

When an alert is investigated, AI-Alertmanager adds the following labels:

- `ai_investigation`: Summary of the investigation result (truncated to 200 chars)
- `ai_investigation_status`: Status of the investigation (`pending`, `investigating`, `completed`, `failed`)

**Example enriched alert:**
```json
{
  "labels": {
    "alertname": "HighMemoryUsage",
    "severity": "warning",
    "namespace": "production",
    "ai_investigation": "Root cause: Memory leak in user service due to unclosed database connections",
    "ai_investigation_status": "completed"
  },
  "annotations": {
    "summary": "High memory usage detected",
    "description": "Memory usage is above 90%"
  }
}
```

## Development

### Install Dependencies

```bash
# Install Poetry
curl -sSL https://install.python-poetry.org | python3 -

# Install dependencies
poetry install
```

### Run Tests

```bash
# Run all tests
poetry run pytest

# Run with coverage
poetry run pytest --cov=app --cov-report=html

# Run specific test file
poetry run pytest tests/test_api.py -v
```

### Run Locally

```bash
# Set environment variables
export AI_ALERTMANAGER_HOLMES_URL=http://localhost:8080
export AI_ALERTMANAGER_LOG_LEVEL=DEBUG

# Run the server
poetry run python -m uvicorn app.main:app --reload --port 9093
```

### Code Quality

```bash
# Format code
poetry run ruff format

# Lint code
poetry run ruff check --fix

# Type checking
poetry run mypy app
```

## Building Docker Image

```bash
# Build the image
docker build -t ai-alertmanager:latest .

# Run the container
docker run -p 9093:9093 \
  -e AI_ALERTMANAGER_HOLMES_URL=http://holmes:8080 \
  ai-alertmanager:latest
```

## Prometheus Configuration

Configure Prometheus to send alerts to AI-Alertmanager:

```yaml
# prometheus.yml
alerting:
  alertmanagers:
    - static_configs:
        - targets:
            - ai-alertmanager:9093
      timeout: 10s
      api_version: v2
```

## Limitations

- **In-Memory Storage**: Currently uses in-memory storage. Alerts are lost on restart. Production deployments should implement persistent storage.
- **No Clustering**: Single instance only. High availability requires external load balancing.
- **Silences Not Implemented**: Silence functionality is not yet implemented.
- **No Inhibition Rules**: Alert inhibition rules are not supported.

## Future Enhancements

- [ ] Persistent storage backend (PostgreSQL, Redis)
- [ ] High availability / clustering support
- [ ] Silence functionality
- [ ] Inhibition rules
- [ ] Webhook receivers
- [ ] Investigation result caching
- [ ] Prometheus metrics export
- [ ] Investigation history and analytics
- [ ] Custom investigation templates
- [ ] Multi-LLM support (beyond HolmesGPT)

## Troubleshooting

### Investigations Not Running

1. Check HolmesGPT connectivity:
```bash
curl http://holmes:8080/healthz
```

2. Check AI-Alertmanager logs:
```bash
kubectl logs -n monitoring -l app=ai-alertmanager
```

3. Verify configuration:
```bash
kubectl get configmap ai-alertmanager-config -n monitoring -o yaml
```

### Alerts Not Appearing

1. Verify Prometheus is sending alerts:
```bash
# Check Prometheus targets
curl http://prometheus:9090/api/v1/targets

# Check Prometheus alertmanagers
curl http://prometheus:9090/api/v1/alertmanagers
```

2. Check AI-Alertmanager is receiving alerts:
```bash
curl http://localhost:9093/api/v2/alerts
```

### High Memory Usage

If you're experiencing high memory usage:

1. Reduce investigation concurrency:
```bash
export AI_ALERTMANAGER_INVESTIGATION_CONCURRENCY=2
```

2. Implement alert cleanup (clear old alerts periodically)

3. Consider implementing persistent storage

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Run tests and linting
6. Submit a pull request

## License

This project is part of HolmesGPT and follows the same license.

## Support

For issues and questions:
- GitHub Issues: https://github.com/robusta-dev/holmesgpt/issues
- Documentation: https://docs.holmesgpt.dev

## Acknowledgments

- Built on top of [HolmesGPT](https://github.com/robusta-dev/holmesgpt)
- Compatible with [Prometheus Alertmanager](https://github.com/prometheus/alertmanager)
- Integrates with [Prometheus Operator](https://github.com/prometheus-operator/prometheus-operator)
