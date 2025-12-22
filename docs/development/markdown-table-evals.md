# Markdown table eval ideas

## 10 candidate scenarios

1. **Pod health snapshot** – summarize pod phase and restart counts across two namespaces into a single markdown table and highlight pods that need attention.
2. **HTTP SLO comparison** – present p99 latency and error rate per service endpoint in a table, sorted by risk, and call out which endpoints are violating targets.
3. **Capacity headroom** – turn node and mount utilization readings into a table with projected days to 95% usage where growth data is provided.
4. **Deployment rollout matrix** – list deployment names, current replicas, and rollout status across namespaces, emphasizing ones stuck in progress.
5. **Config drift audit** – compare desired vs. actual replicas and image tags for services in a table and flag mismatches.
6. **Certificate expirations** – tabulate certificates with their service owner, expiration date, and days until expiry, prioritizing ones expiring soon.
7. **Alert volume by team** – show alert counts per team with on-call rotation notes in a table and surface the highest-volume team.
8. **DB query hotspots** – table of slowest queries with average duration, p99 duration, and affected tables, highlighting the worst offender.
9. **Kafka partition skew** – table of topics with partition count, leader distribution, and skew percentage, identifying topics needing rebalancing.
10. **Backup compliance** – table of services with last backup time, retention target, and gaps, flagging any missing their RPO.

## Top 3 selected

1. **Pod health snapshot** – clear, action-focused table that tests formatting and prioritization from structured operational data.
2. **HTTP SLO comparison** – exercises numerical reporting in table form and requires highlighting the riskiest endpoint.
3. **Capacity headroom** – validates tabular presentation plus a small calculation (projecting time to threshold) to justify table usage.
