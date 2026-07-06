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