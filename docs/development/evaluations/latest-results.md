# ⚡ HolmesGPT LLM Evaluation Fast Benchmark Results

**Generated**: 2026-01-05 15:39 UTC  
**Total Duration**: 3m 4s  
**Iterations**: 1  
**Judge (classifier) model**: gpt-4.1

!!! info "Fast Benchmark"
    **Markers**: `regression or benchmark`<br>
    **Schedule**: Weekly (Sunday 2 AM UTC)<br>
    **Purpose**: Quick regression tests to catch breaking changes

HolmesGPT is continuously evaluated against real-world Kubernetes and cloud troubleshooting scenarios.

If you find scenarios that HolmesGPT does not perform well on, please consider adding them as evals to the benchmark.

## Model Accuracy Comparison

| Model | Pass | Fail | Skip/Error | Total | Success Rate |
|-------|------|------|------------|-------|--------------|
| deepseek-3.1 | 1 | 0 | 0 | 1 | 🟢 100% (1/1) |
| gpt-5 | 0 | 1 | 0 | 1 | 🔴 0% (0/1) |
| gpt-5.1 | 1 | 0 | 0 | 1 | 🟢 100% (1/1) |
| haiku-4.5 | 1 | 0 | 0 | 1 | 🟢 100% (1/1) |
| sonnet-4.5 | 1 | 0 | 0 | 1 | 🟢 100% (1/1) |

## Model Cost Comparison

| Model | Tests | Avg Cost | Min Cost | Max Cost | Total Cost |
|-------|-------|----------|----------|----------|------------|
| gpt-5 | 1 | $0.03 | $0.03 | $0.03 | $0.03 |
| gpt-5.1 | 1 | $0.12 | $0.12 | $0.12 | $0.12 |
| haiku-4.5 | 1 | $0.05 | $0.05 | $0.05 | $0.05 |
| sonnet-4.5 | 1 | $0.19 | $0.19 | $0.19 | $0.19 |

## Model Latency Comparison

| Model | Avg (s) | Min (s) | Max (s) | P50 (s) | P95 (s) |
|-------|---------|---------|---------|---------|---------|
| deepseek-3.1 | 131.8 | 131.8 | 131.8 | 131.8 | 131.8 |
| gpt-5 | 23.4 | 23.4 | 23.4 | 23.4 | 23.4 |
| gpt-5.1 | 73.1 | 73.1 | 73.1 | 73.1 | 73.1 |
| haiku-4.5 | 31.0 | 31.0 | 31.0 | 31.0 | 31.0 |
| sonnet-4.5 | 47.8 | 47.8 | 47.8 | 47.8 | 47.8 |

## Performance by Tag

Success rate by test category and model:

| Tag | deepseek-3.1 | gpt-5 | gpt-5.1 | haiku-4.5 | sonnet-4.5 | Warnings |
|-----|-------|-------|-------|-------|-------|----------|
| [easy](https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/local-benchmark-20260105-153457?c=&search=%7B%22filter%22%3A%20%5B%7B%22text%22%3A%20%22tags%2520includes%2520%255B%2522easy%2522%255D%22%2C%20%22label%22%3A%20%22Tags%2520includes%2520easy%22%2C%20%22originType%22%3A%20%22form%22%7D%5D%7D) | 🟢 100% (1/1) | 🔴 0% (0/1) | 🟢 100% (1/1) | 🟢 100% (1/1) | 🟢 100% (1/1) |  |
| [kubernetes](https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/local-benchmark-20260105-153457?c=&search=%7B%22filter%22%3A%20%5B%7B%22text%22%3A%20%22tags%2520includes%2520%255B%2522kubernetes%2522%255D%22%2C%20%22label%22%3A%20%22Tags%2520includes%2520kubernetes%22%2C%20%22originType%22%3A%20%22form%22%7D%5D%7D) | 🟢 100% (1/1) | 🔴 0% (0/1) | 🟢 100% (1/1) | 🟢 100% (1/1) | 🟢 100% (1/1) |  |
| [one-test](https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/local-benchmark-20260105-153457?c=&search=%7B%22filter%22%3A%20%5B%7B%22text%22%3A%20%22tags%2520includes%2520%255B%2522one-test%2522%255D%22%2C%20%22label%22%3A%20%22Tags%2520includes%2520one-test%22%2C%20%22originType%22%3A%20%22form%22%7D%5D%7D) | 🟢 100% (1/1) | 🔴 0% (0/1) | 🟢 100% (1/1) | 🟢 100% (1/1) | 🟢 100% (1/1) |  |
| [regression](https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/local-benchmark-20260105-153457?c=&search=%7B%22filter%22%3A%20%5B%7B%22text%22%3A%20%22tags%2520includes%2520%255B%2522regression%2522%255D%22%2C%20%22label%22%3A%20%22Tags%2520includes%2520regression%22%2C%20%22originType%22%3A%20%22form%22%7D%5D%7D) | 🟢 100% (1/1) | 🔴 0% (0/1) | 🟢 100% (1/1) | 🟢 100% (1/1) | 🟢 100% (1/1) |  |
| **Overall** | 🟢 100% (1/1) | 🔴 0% (0/1) | 🟢 100% (1/1) | 🟢 100% (1/1) | 🟢 100% (1/1) |  |

## Raw Results

Status of all evaluations across models. Color coding:

- 🟢 Passing 100% (stable)
- 🟡 Passing 1-99%
- 🔴 Passing 0% (failing)
- 🔧 Mock data failure (missing or invalid test data)
- ⚠️ Setup failure (environment/infrastructure issue)
- ⏱️ Timeout or rate limit error
- ⏭️ Test skipped (e.g., known issue or precondition not met)

| Eval ID | [deepseek-3.1](https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/local-benchmark-20260105-153457?c=&search=%7B%22filter%22%3A%20%5B%7B%22text%22%3A%20%22metadata.model%2520%253D%2520%2522deepseek-3.1%2522%22%2C%20%22label%22%3A%20%22metadata.model%2520equals%2520deepseek-3.1%22%2C%20%22originType%22%3A%20%22form%22%7D%5D%7D) | [gpt-5](https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/local-benchmark-20260105-153457?c=&search=%7B%22filter%22%3A%20%5B%7B%22text%22%3A%20%22metadata.model%2520%253D%2520%2522gpt-5%2522%22%2C%20%22label%22%3A%20%22metadata.model%2520equals%2520gpt-5%22%2C%20%22originType%22%3A%20%22form%22%7D%5D%7D) | [gpt-5.1](https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/local-benchmark-20260105-153457?c=&search=%7B%22filter%22%3A%20%5B%7B%22text%22%3A%20%22metadata.model%2520%253D%2520%2522gpt-5.1%2522%22%2C%20%22label%22%3A%20%22metadata.model%2520equals%2520gpt-5.1%22%2C%20%22originType%22%3A%20%22form%22%7D%5D%7D) | [haiku-4.5](https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/local-benchmark-20260105-153457?c=&search=%7B%22filter%22%3A%20%5B%7B%22text%22%3A%20%22metadata.model%2520%253D%2520%2522haiku-4.5%2522%22%2C%20%22label%22%3A%20%22metadata.model%2520equals%2520haiku-4.5%22%2C%20%22originType%22%3A%20%22form%22%7D%5D%7D) | [sonnet-4.5](https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/local-benchmark-20260105-153457?c=&search=%7B%22filter%22%3A%20%5B%7B%22text%22%3A%20%22metadata.model%2520%253D%2520%2522sonnet-4.5%2522%22%2C%20%22label%22%3A%20%22metadata.model%2520equals%2520sonnet-4.5%22%2C%20%22originType%22%3A%20%22form%22%7D%5D%7D) |
|---------|-------|-------|-------|-------|-------|
| [**09_crashpod**](https://github.com/HolmesGPT/holmesgpt/blob/master/tests/llm/fixtures/test_ask_holmes/09_crashpod/test_case.yaml) [🔗](https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/local-benchmark-20260105-153457?c=&search=%7B%22filter%22%3A%20%5B%7B%22text%22%3A%20%22metadata.eval_id%2520%253D%2520%252209_crashpod%2522%22%2C%20%22label%22%3A%20%22metadata.eval_id%2520equals%252009_crashpod%22%2C%20%22originType%22%3A%20%22form%22%7D%5D%7D) | [🟢](https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/local-benchmark-20260105-153457?c=&search=%7B%22filter%22%3A%20%5B%7B%22text%22%3A%20%22span_attributes.name%2520%253D%2520%252209_crashpod%255Bdeepseek-3.1%255D%2522%22%2C%20%22label%22%3A%20%22Name%2520equals%252009_crashpod%255Bdeepseek-3.1%255D%22%2C%20%22originType%22%3A%20%22form%22%7D%5D%7D) | [🔴](https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/local-benchmark-20260105-153457?c=&search=%7B%22filter%22%3A%20%5B%7B%22text%22%3A%20%22span_attributes.name%2520%253D%2520%252209_crashpod%255Bgpt-5%255D%2522%22%2C%20%22label%22%3A%20%22Name%2520equals%252009_crashpod%255Bgpt-5%255D%22%2C%20%22originType%22%3A%20%22form%22%7D%5D%7D) | [🟢](https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/local-benchmark-20260105-153457?c=&search=%7B%22filter%22%3A%20%5B%7B%22text%22%3A%20%22span_attributes.name%2520%253D%2520%252209_crashpod%255Bgpt-5.1%255D%2522%22%2C%20%22label%22%3A%20%22Name%2520equals%252009_crashpod%255Bgpt-5.1%255D%22%2C%20%22originType%22%3A%20%22form%22%7D%5D%7D) | [🟢](https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/local-benchmark-20260105-153457?c=&search=%7B%22filter%22%3A%20%5B%7B%22text%22%3A%20%22span_attributes.name%2520%253D%2520%252209_crashpod%255Bhaiku-4.5%255D%2522%22%2C%20%22label%22%3A%20%22Name%2520equals%252009_crashpod%255Bhaiku-4.5%255D%22%2C%20%22originType%22%3A%20%22form%22%7D%5D%7D) | [🟢](https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/local-benchmark-20260105-153457?c=&search=%7B%22filter%22%3A%20%5B%7B%22text%22%3A%20%22span_attributes.name%2520%253D%2520%252209_crashpod%255Bsonnet-4.5%255D%2522%22%2C%20%22label%22%3A%20%22Name%2520equals%252009_crashpod%255Bsonnet-4.5%255D%22%2C%20%22originType%22%3A%20%22form%22%7D%5D%7D) |
| **SUMMARY** | 🟢 100% (1/1) | 🔴 0% (0/1) | 🟢 100% (1/1) | 🟢 100% (1/1) | 🟢 100% (1/1) |

## Detailed Raw Results

| Eval ID | deepseek-3.1 | gpt-5 | gpt-5.1 | haiku-4.5 | sonnet-4.5 |
|---------|-------|-------|-------|-------|-------|
| [09_crashpod](https://github.com/HolmesGPT/holmesgpt/blob/master/tests/llm/fixtures/test_ask_holmes/09_crashpod/test_case.yaml) [🔗](https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/local-benchmark-20260105-153457?c=&search=%7B%22filter%22%3A%20%5B%7B%22text%22%3A%20%22metadata.eval_id%2520%253D%2520%252209_crashpod%2522%22%2C%20%22label%22%3A%20%22metadata.eval_id%2520equals%252009_crashpod%22%2C%20%22originType%22%3A%20%22form%22%7D%5D%7D) | [🟢 100% (1/1)](https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/local-benchmark-20260105-153457?c=&search=%7B%22filter%22%3A%20%5B%7B%22text%22%3A%20%22span_attributes.name%2520%253D%2520%252209_crashpod%255Bdeepseek-3.1%255D%2522%22%2C%20%22label%22%3A%20%22Name%2520equals%252009_crashpod%255Bdeepseek-3.1%255D%22%2C%20%22originType%22%3A%20%22form%22%7D%5D%7D) / ⏱️ 131.8s | [🔴 0% (0/1)](https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/local-benchmark-20260105-153457?c=&search=%7B%22filter%22%3A%20%5B%7B%22text%22%3A%20%22span_attributes.name%2520%253D%2520%252209_crashpod%255Bgpt-5%255D%2522%22%2C%20%22label%22%3A%20%22Name%2520equals%252009_crashpod%255Bgpt-5%255D%22%2C%20%22originType%22%3A%20%22form%22%7D%5D%7D) / ⏱️ 23.4s / 💰 $0.03 | [🟢 100% (1/1)](https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/local-benchmark-20260105-153457?c=&search=%7B%22filter%22%3A%20%5B%7B%22text%22%3A%20%22span_attributes.name%2520%253D%2520%252209_crashpod%255Bgpt-5.1%255D%2522%22%2C%20%22label%22%3A%20%22Name%2520equals%252009_crashpod%255Bgpt-5.1%255D%22%2C%20%22originType%22%3A%20%22form%22%7D%5D%7D) / ⏱️ 73.1s / 💰 $0.12 | [🟢 100% (1/1)](https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/local-benchmark-20260105-153457?c=&search=%7B%22filter%22%3A%20%5B%7B%22text%22%3A%20%22span_attributes.name%2520%253D%2520%252209_crashpod%255Bhaiku-4.5%255D%2522%22%2C%20%22label%22%3A%20%22Name%2520equals%252009_crashpod%255Bhaiku-4.5%255D%22%2C%20%22originType%22%3A%20%22form%22%7D%5D%7D) / ⏱️ 31.0s / 💰 $0.05 | [🟢 100% (1/1)](https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/local-benchmark-20260105-153457?c=&search=%7B%22filter%22%3A%20%5B%7B%22text%22%3A%20%22span_attributes.name%2520%253D%2520%252209_crashpod%255Bsonnet-4.5%255D%2522%22%2C%20%22label%22%3A%20%22Name%2520equals%252009_crashpod%255Bsonnet-4.5%255D%22%2C%20%22originType%22%3A%20%22form%22%7D%5D%7D) / ⏱️ 47.8s / 💰 $0.19 |

---
*Results are automatically generated and updated weekly. View full traces and detailed analysis in [Braintrust experiment: local-benchmark-20260105-153457](https://www.braintrust.dev/app/robustadev/p/HolmesGPT/experiments/local-benchmark-20260105-153457).*
