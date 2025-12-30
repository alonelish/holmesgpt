# Tempo Trace Performance Comparison

**When to use this runbook:** Invoke it when users or alerts mention slow requests, rising p95/p99 latency, endpoint slowness, or trace-based performance regressions in services reporting to Grafana Tempo (OpenTelemetry).

## Goal
- **Primary Objective:** Diagnose performance degradation visible in distributed traces by comparing slow, typical, and fast requests to isolate latency drivers.
- **Scope:** Applies to services instrumented with OpenTelemetry traces stored in Grafana Tempo (TraceQL and TraceQL metrics available). Focus on Kubernetes workloads where service, namespace, deployment, or pod labels are present.
- **Agent Mandate:** Follow the workflow steps in order—collect baseline metrics, gather representative traces, compare attributes, and validate findings—without skipping steps.
- **Expected Outcome:** Identify where latency accumulates (service, span, dependency, or infrastructure layer), provide evidence from trace comparisons, and outline remediation or escalation steps.

## Workflow for Tempo Trace Performance Diagnosis
1. **Establish investigation context**
   - **Action:** Confirm target service/endpoint, namespace, and timeframe from the alert or user input. If unclear, request clarification before proceeding.
   - **Function Description:** Collect context variables and set a reasonable time window (e.g., last 30–60 minutes) for trace and metric queries.
   - **Parameters:** service/endpoint name, namespace, suspected operation, time window.
   - **Expected Output:** Clear scope (service + route + namespace) and time bounds to reuse across queries.
   - **Success/Failure Criteria:** Success = scope defined; Failure = missing context → ask for details.

2. **Quantify latency using TraceQL metrics**
   - **Action:** Run a time-series TraceQL metrics query for duration percentiles per endpoint/service.
   - **Function Description:** Execute a range metrics query (TraceQL metrics) grouping by route or service to get p50/p90/p99 over time.
   - **Parameters:** TraceQL selector with `duration` and relevant attributes (e.g., `{resource.service.name="<service>"}`) and step sized to stay under result limits.
   - **Expected Output:** Percentile curves showing when latency regressed and which endpoints are affected.
   - **Success/Failure Criteria:** Success = metric series returned; Failure = empty/errored series → simplify selector and retry or widen time window.

3. **Search for slow traces**
   - **Action:** List traces with high duration for the scoped service/endpoint.
   - **Function Description:** Issue a TraceQL search filtered by service/namespace/route plus a duration predicate (e.g., `duration > 1s`) and set an explicit limit.
   - **Parameters:** TraceQL filter combining resource and span attributes (service, namespace, deployment, endpoint) with duration threshold, start/end timestamps, limit (e.g., 20–50), spans-per-span-set if supported.
   - **Expected Output:** Ordered list of slow trace summaries with IDs, start times, durations.
   - **Success/Failure Criteria:** Success = at least a few slow traces; Failure = none found → lower threshold or broaden filters.

4. **Search for baseline (typical/fast) traces**
   - **Action:** Gather representative normal traces for comparison.
   - **Function Description:** Repeat the TraceQL search without duration filter or with a lower bound (e.g., `duration < 500ms`) using the same labels and time window.
   - **Parameters:** Same as prior step but adjust duration predicate/limit.
   - **Expected Output:** Set of baseline trace summaries for healthy behavior.
   - **Success/Failure Criteria:** Success = baseline traces available; Failure = none → broaden window or remove strict filters.

5. **Inspect trace details**
   - **Action:** Fetch full trace data for slow and baseline samples.
   - **Function Description:** Use the trace-by-ID query to retrieve complete spans for selected slow traces (top 3–5) and baseline traces (2–3).
   - **Parameters:** Trace IDs, start/end bounds matching earlier queries.
   - **Expected Output:** Span timelines including attributes (db/system, http status/target, messaging peers), durations, and errors.
   - **Success/Failure Criteria:** Success = full traces returned; Failure = missing data → re-run with exact start/end or confirm trace still retained.

6. **Compare spans and attributes**
   - **Action:** Analyze differences between slow and baseline traces.
   - **Function Description:** Examine span durations, critical path ordering, retries, error tags, remote endpoints, and resource attributes (node/pod/deployment) across samples.
   - **Parameters:** Span fields from fetched traces; focus on outliers (long spans, repeated segments, error status).
   - **Expected Output:** List of deviations (e.g., specific span or dependency consistently slower, queueing spans added, elevated error status codes).
   - **Success/Failure Criteria:** Success = concrete differences identified; Failure = traces look identical → re-check metrics for other endpoints or extend time window.

7. **Validate findings with additional queries (optional)**
   - **Action:** If bottleneck suspected, run a focused TraceQL search or metrics query on the suspect component/attribute.
   - **Function Description:** Query traces/metrics filtered by the problematic attribute (e.g., database system, external host, kubernetes pod/node) to confirm pattern prevalence.
   - **Parameters:** Attribute filter plus time window and reasonable limit/step.
   - **Expected Output:** Corroborating evidence showing the same latency/error hotspot across multiple traces.
   - **Success/Failure Criteria:** Success = pattern repeats; Failure = inconsistent → reassess candidate causes.

## Synthesize Findings
- **Data Correlation:** Combine percentile trends with trace comparisons to pinpoint when and where latency spikes occur (service/endpoint, span type, dependency, or infrastructure label).
- **Pattern Recognition:** Call out recurring slow spans, repeated retries, external calls with high duration, or spans tied to specific pods/nodes/deployments.
- **Prioritization Logic:** Rank causes by frequency and impact (e.g., critical path spans with largest added duration and highest occurrence across slow traces).
- **Evidence Requirements:** Each suspected cause should cite trace IDs, span names, durations, and relevant attributes (service, endpoint, dependency host, pod/node).
- **Example Synthesis:** “Slow traces for `checkout` (trace IDs …) show `POST /payments` span taking 1.8–2.3s vs. 250ms in baseline, with retries to `payments-api` returning HTTP 429—primary latency driver.”

## Recommended Remediation Steps
- **Immediate Actions:** Reduce load on the hotspot (scale replica count for the slow service, enable connection pooling/backoff), and clear obvious retry storms or failing downstreams.
- **Permanent Solutions:** Optimize the identified slow span (e.g., fix database query or cache misses), adjust client timeouts/backoff, and right-size resources for the affected service or node pool.
- **Verification Steps:** Re-run TraceQL metrics and targeted trace searches after changes to confirm percentile improvements and disappearance of slow-span pattern.
- **Documentation References:** Consult Grafana Tempo TraceQL and TraceQL metrics guides, service-specific performance runbooks, and dependency owner docs for the slow component.
- **Escalation Criteria:** Escalate if latency is driven by third-party or shared infrastructure outside ownership, or if no actionable differences emerge after multiple trace comparisons.
- **Post-Remediation Monitoring:** Keep percentile metrics and error rates for the affected endpoints under watch; create alerts on p95/p99 regressions and retries to the identified dependency.
