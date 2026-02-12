"""
Simulated ML model cache service that has a memory leak.

The service caches model inference results but never evicts old entries.
Memory grows steadily over time until the container is OOMKilled.

This is a realistic scenario: an inference cache that grows without bounds
because the eviction policy was accidentally removed in a refactor.
"""
import time
import sys
import logging
import random
import string

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [model-cache] %(message)s",
)
logger = logging.getLogger("model-cache")

# The "cache" that leaks - entries are never evicted
inference_cache = {}
request_count = 0


def generate_cache_key():
    """Generate a realistic-looking cache key."""
    model_id = random.choice(["resnet50", "bert-base", "gpt2-small", "vgg16"])
    input_hash = "".join(random.choices(string.hexdigits[:16], k=12))
    return f"{model_id}:{input_hash}"


def simulate_inference_result():
    """Generate a fake inference result that consumes memory."""
    # Each result is ~50KB of data (simulating embeddings/predictions)
    return {
        "predictions": [random.random() for _ in range(6000)],
        "metadata": {
            "timestamp": time.time(),
            "model_version": "2.1.3",
            "latency_ms": random.uniform(10, 50),
        },
    }


def main():
    global request_count
    logger.info("Model inference cache service starting (v3.2.1)")
    logger.info("Cache eviction policy: DISABLED (performance optimization)")

    while True:
        # Process ~20 "requests" per second, each caching a result
        for _ in range(20):
            key = generate_cache_key()
            result = simulate_inference_result()
            inference_cache[key] = result
            request_count += 1

        cache_size_mb = sys.getsizeof(inference_cache) / (1024 * 1024)
        if request_count % 200 == 0:
            logger.info(
                f"Processed {request_count} requests, cache entries: {len(inference_cache)}, "
                f"approx cache overhead: {cache_size_mb:.1f}MB"
            )

        time.sleep(1)


if __name__ == "__main__":
    main()
