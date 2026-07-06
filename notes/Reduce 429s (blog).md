[Reduce 429 errors on Vertex AI (blog)](https://cloud.google.com/blog/products/ai-machine-learning/reduce-429-errors-on-vertex-ai)

Newer companion to [2024 - handling 429s]. Highest-leverage levers, in order:

1. **Use the `global` endpoint, not regional.** Regional ties you to one region's shared pool; global routes each request to the region with the most spare capacity → **materially lower 429 rate**. Biggest single win for high-volume measurement traffic.
2. **Exponential backoff WITH JITTER.** Google's published example (tenacity): `wait_random_exponential(multiplier=1, max=60)` — randomized `2^x·1s` capped at **60s**, retry until success. No mandated max-retry count.
3. **Smooth traffic within each minute.** Avoid second-level spikes — sharp bursts throttle even when average per-minute usage is low. (Matters for our harness: a tight `asyncio.gather` fan-out is a second-level spike.)
4. **Provisioned Throughput** for anything that needs a capacity guarantee — the only way 429s become someone else's problem. See [Provisioned Throughput] / [SLA].

Context: on DSQ a 429 = "temporary high contention for a specific shared resource" (per-model, per-region), not a fixed ceiling you crossed. Without PT, "if resources aren't available a 429 is returned" — and per [SLA] that 429 **doesn't count against the SLA**.
