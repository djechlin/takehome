[GEAP > Models > Priority PayGo](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/priority-paygo)

- more consistent perf than Standard PayGo without the upfront commitment of Provisioned Throughput.
- Charged per token usage at a higher rate than Standard PayGo.

Ideal for biz crit wls with fluctuating or unpredictable traffic patterns. Examples:

- customer facing assistants
- agentic workflows and cross-agent interactions
- research simulations

(so like, everything?)

Priority PayGo is supported in `global` endpoint only, not regional, for many / most / all models.

Use it via `X-Vertex-AI-LLM-Shared-Request-Type: priority` header.

You find out you did it right via `usageMetadata.trafficType` == `ON_DEMAND_PRIORITY`.

There are ramp limits!!!! Set at org level.

Starting limit is 4M toks / min. Increases by 50% for every 10 minutes of sustained usage.

## Verified + corrected from research (2026-07-06)

- **4M tok/min is Flash-specific.** Flash & Flash-Lite start at 4M/min; **Gemini Pro starts at 1M/min** (same 50%/10-min ramp). Ramp confirmed org-level. Source: same page.
- 🔴 **Exceeding the ramp limit does NOT 429 — it SILENTLY DOWNGRADES the request to Standard PayGo** (different billing rate AND different reliability, i.e. back to best-effort DSQ). Same on temporary over-capacity.
  - **Load-test gotcha:** a big priority burst can silently fall back to DSQ mid-run. Check `usageMetadata.trafficType` per response — if it flips from `ON_DEMAND_PRIORITY` to on-demand, you're being downgraded, not throttled. Cost + reliability both change under you without an error.
- Ramp limits are a **Priority PayGo** construct only — they do NOT apply to Standard PayGo / DSQ (which has no ramp schedule, just the shared pool).
