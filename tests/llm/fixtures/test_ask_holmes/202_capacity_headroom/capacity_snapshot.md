Node and mount utilization:

- worker-a: /var/lib/docker at 82% disk, 71% inodes. Growth flat after image prune.
- worker-b: /var/lib/docker at 65% disk, 55% inodes. Stable.
- db-node-0: /var/lib/postgresql at 91% disk, 88% inodes with ~3% daily growth.

Threshold to watch: 95% disk usage.
