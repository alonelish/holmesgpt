Latest SLO snapshot:

- orders-api: p99 latency 1420 ms, error rate 3.8% with timeouts reaching inventory.
- payments-api: p99 latency 880 ms, error rate 0.6% with occasional retry spikes.
- catalog-api: p99 latency 620 ms, error rate 0.2% after cache warmup.

Targets: p99 latency under 900 ms and error rate under 1%.
