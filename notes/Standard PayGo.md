[GEAP > Standard PayGo](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/standard-paygo)

A consumption option for util GEAP including Gemini model family.

To provide more predictable perf for scalable WLs, incorporates a usage tier system.

"Dynamically adjusts your org's baseline throughput capacity, based on its total spend on eligible AP services over a rolling 30-day period."

Auto-promotes to higher tiers as org's spend grows.

For WLs needing more consistent perf, consider [Priority PayGo]. 

For dedicated and assured capacity see [Provisioned Throughput].

Each S-PayGo tier aims to provide a Baseline Throughput measured in TPM, should be a predictable performance floor.

Limits are based on requests sent to the global endpoint (idk what this means).

Using global endpoint is best practice, because larger, multi-region pool of throughput capacity, and allows routing to the best location.

AP lets traffic burst beyond this limit on best-effort basis.

To optimize perf and minimize likelihood of these errors, best practice to smooth your traffic throughout each minute. Avoid second-level spikes, this can throttle, even if your average per-minute usage is low.

There's three tiers based on customer spend 30d:

- $10 - 250: 2M tok
- $250 - $2000 - 4M tok
- >$2000 - 10M tok

(so you might actually bump tiers in the middle of load testing)

Alleges a 30k RPM per model per region limit. (Now we're throttled at "request" not "token.")

## Corrected from research (2026-07-06)

- The 30k figure is actually **30,000 online inference requests / min PER PROJECT PER REGION** (default quota), *not* per-model. A 2nd region gives another 30k. Source: [gen AI quotas](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/quotas). ⚠️ table is JS-rendered — **confirm the exact number in the console for our model+region**.
- "Limits based on global-endpoint requests" = the shared pool is **per-model, per-region**; the **global endpoint routes each request to whichever region has the most spare capacity**, so you draw from a bigger multi-region pool → **materially lower 429 rate**. This is the single highest-leverage change for high-volume traffic. See [Reduce 429s (blog)].
- "99.5% within threshold" note: that within-threshold high-priority lane is a **best-effort SLO target, not the contractual SLA**, and 429s don't count against the SLA. See [SLA].
