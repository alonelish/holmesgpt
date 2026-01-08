---
name: eval-development
description: Guide for creating and debugging HolmesGPT LLM evaluation tests. Use this when asked to create new evals, debug failing evals, or modify existing evaluation tests.
---

## Testing Framework

**Three-tier testing approach**:

1. **Unit Tests** (`tests/`): Standard pytest tests for individual components
2. **Integration Tests**: Test toolset integrations
3. **LLM Evaluation Tests** (`tests/llm/`): End-to-end tests using fixtures

**LLM Test Structure**:
- `tests/llm/fixtures/test_ask_holmes/`: 53+ test scenarios with YAML configs
- Each test has expected outputs validated by LLM-as-judge
- Supports Braintrust integration for result tracking

**Running LLM Tests**:
```bash
# Run all LLM tests
poetry run pytest -m 'llm' --no-cov

# Run specific test - IMPORTANT: Use -k flag, NOT full test path!
# CORRECT - use -k flag with test name pattern:
poetry run pytest -m 'llm' -k "09_crashpod" --no-cov
poetry run pytest tests/llm/test_ask_holmes.py -k "114_checkout_latency" --no-cov

# WRONG - DO NOT specify full test path with brackets:
# poetry run pytest tests/llm/test_ask_holmes.py::test_ask_holmes[114_checkout_latency_tracing_rebuild-gpt-4o]
# This syntax fails when environment variables are passed!

# Run regression tests (easy marker) - all should pass with ITERATIONS=10
poetry run pytest -m 'llm and easy' --no-cov
ITERATIONS=10 poetry run pytest -m 'llm and easy' --no-cov

# Run tests in parallel
poetry run pytest tests/llm/ -n 6

# Test with different models
# Note: When using Anthropic models, set CLASSIFIER_MODEL to OpenAI (Anthropic not supported as classifier)
MODEL=anthropic/claude-sonnet-4-20250514 CLASSIFIER_MODEL=gpt-4.1 poetry run pytest tests/llm/test_ask_holmes.py -k "test_name"

# Setting environment variables - IMPORTANT:
# Environment variables must be set BEFORE the poetry command, NOT as pytest arguments
# CORRECT:
EVAL_SETUP_TIMEOUT=600 poetry run pytest -m 'llm' -k "slow_test" --no-cov

# WRONG - this won't work:
# poetry run pytest EVAL_SETUP_TIMEOUT=600 -m 'llm' -k "slow_test"
```

### Evaluation CLI Reference

**Custom Pytest Flags**:
- `--skip-setup`: Skip before_test commands (useful for iterative testing)
- `--skip-cleanup`: Skip after_test commands (useful for debugging)

**Environment Variables**:
- `MODEL`: LLM model(s) to use - supports comma-separated list (e.g., `gpt-4.1` or `gpt-4.1,anthropic/claude-sonnet-4-20250514`)
- `CLASSIFIER_MODEL`: Model for scoring answers (defaults to MODEL)
- `RUN_LIVE=true`: Execute real commands (now enabled by default)
- `ITERATIONS=<number>`: Run each test multiple times
- `UPLOAD_DATASET=true`: Sync dataset to Braintrust
- `EXPERIMENT_ID`: Custom experiment name for tracking
- `BRAINTRUST_API_KEY`: Enable Braintrust integration
- `ASK_HOLMES_TEST_TYPE`: Controls message building flow in ask_holmes tests
  - `cli` (default): Uses `build_initial_ask_messages` like the CLI ask() command (skips conversation history tests)
  - `server`: Uses `build_chat_messages` with ChatRequest for server-style flow

**Common Evaluation Patterns**:

```bash
# Run tests multiple times for reliability
ITERATIONS=100 poetry run pytest tests/llm/test_ask_holmes.py -k "flaky_test"

# Model comparison workflow
EXPERIMENT_ID=gpt41_baseline MODEL=gpt-4.1 poetry run pytest tests/llm/ -n 6
EXPERIMENT_ID=claude_opus41_test MODEL=anthropic/claude-opus-4-1-20250805 CLASSIFIER_MODEL=gpt-4.1 poetry run pytest tests/llm/ -n 6

# Debug with verbose output
poetry run pytest -vv -s tests/llm/test_ask_holmes.py -k "failing_test" --no-cov

# List tests by marker
poetry run pytest -m "llm and not network" --collect-only -q

# Test marker combinations
poetry run pytest -m "llm and easy" --no-cov  # Regression tests
poetry run pytest -m "llm and not easy" --no-cov  # Non-regression tests
```

## Tag Management Guidelines

**Before adding new tags**:
1. Check existing tags in `pyproject.toml` markers section
2. Ask user permission for new tags
3. Use descriptive, hyphenated names (e.g., `grafana-dashboard`, not `grafana_dashboard`)

**Tag naming conventions**:
- Service-specific: `grafana-dashboard`, `prometheus-metrics`, `loki`
- Functionality: `question-answer`, `chain-of-causation`
- Difficulty: `easy`, `medium`, `hard`
- Infrastructure: `kubernetes`, `database`, `traces`

**Adding new tags workflow**:
1. Add tag to `pyproject.toml` markers section with description
2. Apply tag to relevant test files
3. Verify tag filtering works: `pytest -m "new-tag" --collect-only`

**Available Test Markers (same as eval tags)**:
Check in pyproject.toml and NEVER use a marker/tag that doesn't exist there. Ask the user before adding a new one.

**Important**: The `regression` marker identifies critical tests that must always pass in CI/CD. The `easy` marker is a legacy marker that contains broader regression tests.

**Test Infrastructure Notes**:
- All test state tracking uses pytest's `user_properties` to ensure compatibility with pytest-xdist parallel execution
- Test results are stored in `user_properties` and aggregated in the terminal summary
- This design ensures tests work correctly when run in parallel with `-n` flag
- **Important for LLM tests**: Each test must use a dedicated namespace `app-<testid>` (e.g., `app-01`, `app-02`) to prevent conflicts when tests run simultaneously
- All pod names must be unique across tests (e.g., `giant-narwhal`, `blue-whale`, `sea-turtle`) - never reuse pod names between tests
- **Resource naming in evals**: Never use names that hint at the problem or expected behavior (e.g., avoid `broken-pod`, `test-project-that-does-not-exist`, `crashloop-app`). Use neutral names that don't give away what the LLM should discover

## Eval Notes

### Creating New Eval Tests

**Test Structure:**
- Use sequential test numbers: check existing tests for next available number
- Required files: `test_case.yaml`, infrastructure manifests, `toolsets.yaml` (if needed)
- Use dedicated namespace per test: `app-<testid>` (e.g., `app-177`)
- All resource names must be unique across tests to prevent conflicts

**Tags:**
- **CRITICAL**: Only use valid tags from `pyproject.toml` - invalid tags cause test collection failures
- Check existing tags before adding new ones, ask user permission for new tags

**Cloud Service Evals (No Kubernetes Required)**:
- Evals can test against cloud services (Elasticsearch, external APIs) directly via environment variables
- Faster setup (<30 seconds vs minutes for K8s infrastructure)
- `before_test` creates test data in the cloud service, `after_test` cleans up
- Use `toolsets.yaml` to configure the toolset with env var references: `url: "{{ env.ELASTICSEARCH_URL }}"`
- **CI/CD secrets**: When adding evals for a new integration, you must add the required environment variables to `.github/workflows/eval-regression.yaml` in the "Run tests" step. Tell the user which secrets they need to add to their GitHub repository settings (e.g., `ELASTICSEARCH_URL`, `ELASTICSEARCH_API_KEY`).
- **HTTP request passthrough**: The root `conftest.py` has a `responses` fixture with `autouse=True` that mocks ALL HTTP requests by default. When adding a new cloud integration, you MUST add the service's URL pattern to the passthrough list in `conftest.py` (search for `rsps.add_passthru`). Use `re.compile()` for pattern matching (e.g., `rsps.add_passthru(re.compile(r"https://.*\.cloud\.es\.io"))`).

**User Prompts & Expected Outputs:**
- **Be specific**: Test exact values like `"The dashboard title is 'Home'"` not generic `"Holmes retrieves dashboard"`
- **Match prompt to test**: User prompt must explicitly request what you're testing
  - BAD: `"Get the dashboard"`
  - GOOD: `"Get the dashboard and tell me the title, panels, and time range"`
- **Anti-cheat prompts**: Don't use technical terms that give away solutions
  - BAD: `"Find node_exporter metrics"`
  - GOOD: `"Find CPU pressure monitoring queries"`
- **Test discovery, not recognition**: Holmes should search/analyze, not guess from context
- **Ruling out hallucinations is paramount**: When choosing between test approaches, prefer the one that rules out hallucinations:
  - **Best**: Check specific values that can only be discovered by querying (e.g., unique IDs, injected error codes, exact counts)
  - **Acceptable**: Use `include_tool_calls: true` to verify the tool was called when output values are too generic to rule out hallucinations
  - **Bad**: Check generic output patterns that an LLM could plausibly guess (e.g., "cluster status is green/yellow/red", "has N nodes")
- **`include_tool_calls: true`**: Use when expected output is too generic to be hallucination-proof. Prefer specific answer checking when possible, but verifying tool calls is better than a test that can't rule out hallucinations.
  ```yaml
  # Use when values are generic (cluster health could be guessed)
  include_tool_calls: true
  expected_output:
    - "Must call elasticsearch_cluster_health tool"
    - "Must report cluster status"
  ```

**Infrastructure Setup:**
- **Don't just test pod readiness** - verify actual service functionality
- Poll real API endpoints and check for expected content (e.g., `"title":"Home"`, `"type":"welcome"`)
- **CRITICAL**: Use `exit 1` when setup verification fails to fail the test early
- **Never use `:latest` container tags** - use specific versions like `grafana/grafana:12.3.1`

### Running and Testing Evals

## 🚨 CRITICAL: Always Test Your Changes

**NEVER submit test changes without verification**:

### Required Testing Workflow:
1. **Setup Phase**: `poetry run pytest -k "test_name" --only-setup --no-cov`
2. **Full Test**: `poetry run pytest -k "test_name" --no-cov`
3. **Verify Results**: Ensure 100% pass rate and expected behavior

### When to Test:
- ✅ After creating new tests
- ✅ After modifying existing tests
- ✅ After refactoring shared infrastructure
- ✅ After performance optimizations
- ✅ After adding/changing tags

### Red Flags - Never Skip Testing:
- ❌ "The changes look good" without running
- ❌ "It's just a small change"
- ❌ "I'll test it later"

**Testing is Part of Development**: Testing is not optional - it's an integral part of the development process. Untested code is broken code.

**Testing Methodology:**
- Phase 1: Test setup with `--only-setup` flag first
- Phase 2: Run full test after confirming setup works
- Use background execution for long tests: `nohup ... > logfile.log 2>&1 &`
- Handle port conflicts: clean up previous test port forwards before running

**Common Flags:**
- `--skip-cleanup`: Keep resources after test (useful for debugging setup)
- `--skip-setup`: Skip before_test commands (useful for iterative testing)

## Shared Infrastructure Pattern

**When to use shared infrastructure**:
- Multiple tests use the same service (Grafana, Loki, Prometheus)
- Service configuration is standardized across tests

**Implementation**:
```bash
# Create shared manifest in tests/llm/fixtures/shared/servicename.yaml
# Use in tests:
kubectl apply -f ../../shared/servicename.yaml -n app-<testid>
```

**Benefits**:
- Single place for version updates
- Consistent configuration across tests
- Reduced maintenance overhead
- Follows established pattern (Loki, Prometheus, Grafana)

## Setup Verification Best Practices

**Prefer kubectl exec over port forwarding for setup verification**:
```bash
# GOOD - kubectl exec pattern (no port conflicts)
kubectl exec -n namespace deployment/service -- wget -q -O- http://localhost:port/health

# AVOID - port forward for setup verification (causes conflicts)
kubectl port-forward svc/service port:port &
curl localhost:port/health
kill $PORTFWD_PID
```

**Performance optimization guidelines**:
- Use `sleep 1` instead of `sleep 5` for most retry loops
- Remove sleeps after straightforward operations (port forward start)
- Reduce timeout values: 60s for pod readiness, 30s for API verification
- Question every sleep - many are unnecessary

**Race Condition Handling:**
Never use bare `kubectl wait` immediately after resource creation. Use retry loops:
```bash
# WRONG - fails if pod not scheduled yet
kubectl apply -f deployment.yaml
kubectl wait --for=condition=ready pod -l app=myapp --timeout=300s

# CORRECT - retry loop handles race condition
kubectl apply -f deployment.yaml
POD_READY=false
for i in {1..60}; do
  if kubectl wait --for=condition=ready pod -l app=myapp --timeout=5s 2>/dev/null; then
    echo "✅ Pod is ready!"
    POD_READY=true
    break
  fi
  sleep 1
done
if [ "$POD_READY" = false ]; then
  echo "❌ Pod failed to become ready after 60 seconds"
  kubectl logs -l app=myapp --tail=20  # Diagnostic info
  exit 1  # CRITICAL: Fail the test early
fi
```

### Eval Best Practices

**Realism:**
- No fake/obvious logs like "Memory usage stabilized at 800MB"
- No hints in filenames like "disk_consumer.py" - use realistic names like "training_pipeline.py"
- No error messages that give away it's simulated like "Simulated processing error"
- Use real-world scenarios: ML pipelines with checkpoint issues, database connection pools
- Resource naming should be neutral, not hint at the problem (avoid "broken-pod", "crashloop-app")

**Architecture:**
- Implement full architecture even if complex (e.g., use Loki for log aggregation, not simplified alternatives)
- Proper separation of concerns (app → file → Promtail → Loki → Holmes)
- **ALWAYS use Secrets for scripts**, not inline manifests or ConfigMaps
- Use minimal resource footprints (reduce memory/CPU for test services)

**Anti-Cheat Testing Guidelines:**
- **Prevent Domain Knowledge Cheats**: Use neutral, application-specific names instead of obvious technical terms
  - Example: "E-Commerce Platform Monitoring" not "Node Exporter Full"
  - Example: "Payment Service Dashboard" not "MySQL Error Dashboard"
  - Add source comments: `# Uses Node Exporter dashboard but renamed to prevent cheats`
- **Resource Naming Rules**: Avoid hint-giving names
  - Use realistic business context: "checkout-api", "user-service", "inventory-db"
  - Avoid obvious problem indicators: "broken-pod" → "payment-service-1"
  - Test discovery ability, not pattern recognition
- **Prompt Design**: Don't give away solutions in prompts
  - BAD: "Find the node_pressure_cpu_waiting_seconds_total query"
  - GOOD: "Find the Prometheus query that monitors CPU pressure waiting time"
  - Test Holmes's search/analysis skills, not domain knowledge shortcuts

**Configuration:**
- Custom runbooks: Add `runbooks` field in test_case.yaml (`runbooks: {}` for empty catalog)
- Custom toolsets: Create separate `toolsets.yaml` file (never put in test_case.yaml)
- Toolset config must go under `config` field:
```yaml
toolsets:
  grafana/dashboards:
    enabled: true
    config:  # All toolset-specific config under 'config'
      url: http://localhost:10177
```
