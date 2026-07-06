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