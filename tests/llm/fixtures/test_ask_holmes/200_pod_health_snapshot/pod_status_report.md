Daily cluster check:

- Namespace `payments`:
  - checkout-0 — Running, 0 restarts
  - checkout-1 — CrashLoopBackOff, 5 restarts (failing during startup probe)
  - payments-worker-0 — Running, 2 restarts (cleared after transient throttling)

- Namespace `support`:
  - chat-0 — Running, 0 restarts
  - chat-1 — Running, 0 restarts
  - notifier-0 — Pending, 0 restarts (waiting for a node with the required gpu=true label)
