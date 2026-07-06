[Vertex context caching blog](https://cloud.google.com/blog/products/ai-machine-learning/vertex-ai-context-caching) · [2.5 implicit caching](https://developers.googleblog.com/gemini-2-5-models-now-support-implicit-caching/) · [overview](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/context-cache/context-cache-overview)

- save and reuse precomputed input tokens
- pay 10% of standard input token cost (90% off) on **Vertex** for 2.5+
- implicit = automatic, enabled by default. explicit = say what to cache
- both support global and regional
- implicit is integrated with PT

## Added from research (2026-07-06)

- **Implicit caching ON by default** for all projects, all Gemini 2.5+. No clean per-request Vertex toggle to disable; you avoid it by not creating explicit caches + varying the prefix. Explicit caching is opt-in.
- **Cost:** implicit = **no storage cost**. Explicit = adds hourly storage cost per 1M tokens by TTL + one-time create fee.
- **Min tokens (Vertex): 2,048.** ⚠️ Do NOT cross-apply the **Gemini Developer API** numbers (1,024 tok / 75% discount) — different platform.
- **Implicit hits are best-effort, not guaranteed** (put static content at the *start*, request-specific at the *end*; send similar prefixes close in time).

### 🟢 Resolves the TODO.md worry ("caching may interfere... fuzz your input?")

- **Caching does NOT bias the sampled output.** It stores the input-prefix KV representation, not the response. The sampler still runs fresh every call → output distribution unchanged. A cache hit changes **cost + latency only**, not what the model says.
- ⇒ Firing the same prompt thousands of times to measure brand-mention frequency is **NOT** biased by caching. Determinism levers are `temperature`/`top_p`/`seed` (orthogonal to caching).
- Only reason to fuzz input: if you want each sample to be an **independently full-billed** computation (avoid the 10% cached rate skewing cost accounting). Statistically it doesn't matter.

### ⚠️ Discrepancy to resolve

Existing note says "implicit is integrated with PT," but [use-PT](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/provisioned-throughput/use-provisioned-throughput) says **context caching is NOT supported on PT traffic** (a cached-content request is served as pay-as-you-go). Possibly implicit-vs-explicit differ, or docs changed. **Verify before relying on cache savings under PT.**
