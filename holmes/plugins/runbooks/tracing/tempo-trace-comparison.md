# Trace Comparison for Latency and Errors

**When to use this runbook:** Invoke it when users or alerts mention slow requests, rising p95/p99 latency, elevated error rates (4xx/5xx), retry storms, or trace-based performance/regression signals in any supported tracing backend (e.g., Tempo, Datadog, or other OpenTelemetry-compatible systems).

## Goal
- **Primary Objective:** Diagnose latency or error spikes visible in distributed traces by comparing slow/error traces with typical/fast/healthy traces to isolate the driver.
- **Scope:** Applies to services instrumented with distributed tracing in supported backends. Focus on common resource attributes (service, namespace, deployment, pod/node) and span attributes (route, status, database/external calls). Works across tracing providers as long as search and trace-fetch tools are available.
- **Agent Mandate:** Follow the workflow steps in order—collect baseline signals, gather representative traces, compare attributes, and validate findings—without skipping steps.
- **Expected Outcome:** Identify where latency or errors accumulate (service, span, dependency, or infrastructure layer), provide evidence from trace comparisons, and outline remediation or escalation steps.

## Workflow for Trace Performance/Error Diagnosis
1. **Establish investigation context**
   - **Action:** Confirm target service/endpoint, namespace, and timeframe from the alert or user input. If unclear, request clarification before proceeding.
   - **Function Description:** Collect context variables and set a reasonable time window (e.g., last 30–60 minutes) for trace and metric queries.
   - **Parameters:** service/endpoint name, namespace, suspected operation, time window.
   - **Expected Output:** Clear scope (service + route + namespace) and time bounds to reuse across queries.
   - **Success/Failure Criteria:** Success = scope defined; Failure = missing context → ask for details.

2. **Quantify symptoms with trace-derived signals (latency/errors)**
   - **Action:** Use tracing backend analytics or metrics (if available) to chart latency percentiles and error rates over time for the scoped service/endpoint.
   - **Function Description:** Run a time-series query or analytics view that groups by route/service and returns latency percentiles (p50/p90/p99) and error counts/rates across the chosen window. If the backend lacks metrics, sample a rolling set of traces to approximate the pattern (counts of slow/error traces per interval).
   - **Parameters:** Service/endpoint filter, optional namespace/deployment/pod filter, time window, and step/interval if supported.
   - **Expected Output:** Trend showing when latency/errors regressed and which routes or services are affected.
   - **Success/Failure Criteria:** Success = visible trend for latency and/or errors; Failure = empty/errored query → simplify filters, widen window, or fall back to sampling traces.

3. **Search for problematic traces (slow or error)**
   - **Action:** List traces exhibiting the symptom (high duration or error status) for the scoped service/endpoint.
   - **Function Description:** Use the tracing search tool to filter by service/namespace/route and add either a duration threshold (e.g., > target SLO) or error/exception status. Set a clear limit to retrieve a manageable sample.
   - **Parameters:** Service/route filter, namespace/deployment/pod filters, symptom filter (duration > threshold or status/error flag), start/end timestamps, and result limit (e.g., 20–50).
   - **Expected Output:** Ordered list of slow/error trace summaries with IDs, start times, durations, and error flags if available.
   - **Success/Failure Criteria:** Success = at least a few qualifying traces; Failure = none found → lower threshold, broaden filters, or widen the window.

4. **Search for baseline (typical/fast/success) traces**
   - **Action:** Gather representative healthy traces for comparison.
   - **Function Description:** Repeat the trace search with a relaxed duration filter (or no duration constraint) and require success status if errors are the focus. Keep other filters the same to ensure comparability.
   - **Parameters:** Same filters as above but targeting normal/fast traces or success-only spans; set a limit for comparison samples.
   - **Expected Output:** Set of baseline trace summaries that represent healthy behavior.
   - **Success/Failure Criteria:** Success = baseline traces available; Failure = none → broaden time window or adjust filters to include successful requests.

5. **Inspect trace details**
   - **Action:** Fetch full trace data for slow/error samples and baseline samples.
   - **Function Description:** Use the trace-by-ID tool to retrieve complete spans for selected slow/error traces (top 3–5) and baseline traces (2–3).
   - **Parameters:** Trace IDs, start/end bounds matching earlier queries.
   - **Expected Output:** Span timelines including attributes (db/system, http status/target, messaging peers), durations, retries, and errors/exceptions.
   - **Success/Failure Criteria:** Success = full traces returned; Failure = missing data → re-run with exact start/end or confirm trace retention window.

6. **Compare spans and attributes**
   - **Action:** Analyze differences between slow/error traces and baseline traces.
   - **Function Description:** Examine critical path ordering, longest spans, retries, error tags/status codes, remote endpoints, and resource attributes (node/pod/deployment) across samples. Identify spans that repeatedly dominate latency or consistently fail.
   - **Parameters:** Span fields from fetched traces; focus on outliers (long spans, repeated segments, error status, retries/backoff patterns).
   - **Expected Output:** List of deviations (e.g., specific span or dependency consistently slower or failing, queueing spans added, elevated error status codes).
   - **Success/Failure Criteria:** Success = concrete differences identified; Failure = traces look identical → check other endpoints/services or widen the window.

7. **Validate findings with additional queries (optional)**
   - **Action:** If a bottleneck or failing dependency is suspected, run a focused search or analytics query on that attribute across the same window (or slightly wider).
   - **Function Description:** Query traces or trace analytics filtered by the problematic attribute (e.g., database system, external host, HTTP route, Kubernetes pod/node) to confirm pattern prevalence and frequency.
   - **Parameters:** Attribute filter plus time window and reasonable limit/step.
   - **Expected Output:** Corroborating evidence showing the same latency/error hotspot across multiple traces.
   - **Success/Failure Criteria:** Success = pattern repeats; Failure = inconsistent → reassess candidate causes or broaden scope.

## Synthesize Findings
- **Data Correlation:** Combine latency/error trends with trace comparisons to pinpoint when and where the problem occurs (service/endpoint, span type, dependency, or infrastructure label).
- **Pattern Recognition:** Call out recurring slow spans, repeated retries, elevated error codes, external calls with high duration, or spans tied to specific pods/nodes/deployments.
- **Prioritization Logic:** Rank causes by frequency and impact (e.g., critical path spans that add the most latency or fail most often across samples).
- **Evidence Requirements:** Each suspected cause should cite trace IDs, span names, durations/error status, and relevant attributes (service, endpoint, dependency host, pod/node).
- **Example Synthesis:** “Slow traces for `checkout` (trace IDs …) show `POST /payments` span taking 1.8–2.3s vs. 250ms in baseline, with retries to `payments-api` returning HTTP 429—primary latency driver.”

## Recommended Remediation Steps
- **Immediate Actions:** Reduce load on the hotspot (scale replicas, throttle noisy callers), enable backoff for failing dependencies, and clear retry storms or stuck queues.
- **Permanent Solutions:** Optimize the identified slow/failing span (e.g., fix database query or cache misses), fix downstream errors, adjust client/server timeouts/backoff, and right-size resources for the affected service or node pool.
- **Verification Steps:** Re-run tracing analytics/searches after changes to confirm latency/error improvements and disappearance of the problematic span pattern.
- **Documentation References:** Consult tracing backend docs (e.g., Tempo, Datadog APM) plus service-specific performance/error runbooks and dependency owner docs.
- **Escalation Criteria:** Escalate if the issue is driven by a third-party/shared dependency outside ownership, or if no actionable differences emerge after multiple trace comparisons.
- **Post-Remediation Monitoring:** Keep latency percentiles and error rates for the affected endpoints under watch; create alerts on p95/p99 regressions, retries, and error surges targeting the identified dependency.
