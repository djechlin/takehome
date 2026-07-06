[Vertex AI > Provisioned Throughput](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/provisioned-throughput)

PT = reserve dedicated capacity. The escape hatch from DSQ best-effort. **The only Gemini tier with a real (financially-backed) SLA** — see [SLA].

## GSU (Generative AI Scale Unit)

- Standard unit of reserved capacity. Your ceiling = `GSUs purchased × throughput/GSU`, enforced per request per quota window.
- A **burndown rate** normalizes input/output tokens (+ images/chars) into one unit so a GSU means the same across models.
- **Gemini 2.5 Flash: 1 GSU ≈ 2,690 tokens/sec** (blended). 25 GSUs → ~67,250 tok/s.
  [supported-models](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/provisioned-throughput/supported-models)

## Burndown — output costs multiples of input

- `normalized = input_tok × input_rate + output_tok × output_rate`, then ÷ (tok/s per GSU).
- Confirmed on **2.0 Flash**: 1 input tok = 1 unit, **1 output tok = 4 units**, 3,360 tok/s/GSU.
- **Load-test implication: size GSUs by OUTPUT-token volume, not request count.** Our workload (dynamic thinking + long list output) is output-heavy → burns GSU fast.
- ⚠️ Exact 2.5-Flash input:output multipliers live in the (JS-rendered) supported-models table — **verify in browser / GSU estimator**.

## Commitment

- Terms: **1-week** (select models, no auto-renew), **monthly** (auto-renew), yearly. **Cannot cancel mid-term.**
- Minimum GSU + increment is per-model in the supported-models table — ⚠️ couldn't extract exact 2.5-Flash min; verify.
- **$/GSU is NOT on the public pricing page** — via GSU estimator / sales only.

## Overflow behavior (matters for testing the reserved ceiling)

Controlled by header `X-Vertex-AI-LLM-Request-Type`:
- **no header (default): PT first, overflow spills to pay-as-you-go** — silent, billed on-demand, no back-pressure.
- `dedicated` = PT only. Exceed your order → **HTTP 429** (hard ceiling). ← use this to actually load-test the reserved ceiling.
- `shared` = force pay-as-you-go, bypass PT.
- Overflow can target Priority PayGo via `X-Vertex-AI-LLM-Shared-Request-Type: priority`.
- ⚠️ **Context caching is NOT supported on PT traffic** — a cached-content request is served as pay-as-you-go.
  [use-PT](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/provisioned-throughput/use-provisioned-throughput) · [429 on PT](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/provisioned-throughput/error-code-429)

## SLA — see [SLA]

PT adds a **99% Monthly Latency Target Attainment** SLO (p50 TPS above a per-model target, e.g. 60/80/110 TPS). Applies to PT only. Valid requests must be **streaming, HTTP 200, >75 output tokens**. **Single-Zone PT (SZPT) is excluded** from the SLA.
