# Gemini SLA — the headline finding

**Two different "99.5%" numbers exist and they are NOT the same thing.** Don't conflate them.

## 1. DSQ within-threshold "99.5% SLO" — a doc TARGET, not a contract

- [DSQ docs](https://cloud.google.com/vertex-ai/generative-ai/docs/dynamic-shared-quota): on-demand requests **within your TPS threshold** get a high-priority lane *"targeting a 99.5% SLO."* Over threshold → best-effort, can 429.
- "Targeting" = **best-effort documentation target. NOT financially backed. No credits if missed.**
- This is the number the WRITEUP's "fast lane" refers to. It's real as a *performance* target — just not an enforceable SLA.

## 2. The contractual Gemini SLA — 99.5% uptime, but counts only 5XX

[Gemini Online Inference API SLA](https://cloud.google.com/vertex-ai/generative-ai/sla)

- Guarantees **99.5% Monthly Uptime** for `generateContent`/`streamGenerateContent` (95% for "shorter availability" models). Coincidentally the same 99.5% number.
- **"Error Rate" = HTTP 5XX only.** `Downtime` = >5% error rate sustained ≥5 consecutive minutes.
- **⇒ 429 RESOURCE_EXHAUSTED is a 4XX → EXCLUDED from the SLA math entirely.** Confirmed verbatim in [429 doc](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/provisioned-throughput/error-code-429): on a 429 *"the request isn't counted against your error rate as described in your SLA."*
- Exclusion (c)(v): also excludes errors from "quotas applied by the system," preview features, and Grounding with Google Search.

### 🔴 MOST CONCERNING (whole project)

**On pay-as-you-go / DSQ, the single most common production failure — 429 from shared-pool contention — is contractually invisible.** It can never breach either 99.5% number and never earns credits. The "good SLA fast lane" is best-effort. A *backed* availability guarantee for Gemini effectively requires **[Provisioned Throughput]**.

This also reframes the load test: "I failed to DOS Vertex" is even weaker evidence than it looks, because even if we *had* seen 429s, Google owes us nothing for them on DSQ. The 429 rate is a **business/latency risk we self-insure**, not an SLA breach.

## 3. PT-only latency SLO

- PT adds a **99% Monthly Latency Target Attainment** SLO — p50 tokens/sec above a per-model Latency Target (e.g. 60 / 80 / 110 TPS). PT consumption model only; does not apply to on-demand.
- Valid requests must be **streaming, HTTP 200, and >75 output tokens** — small/non-streaming calls are outside the guarantee.
- **Single-Zone PT (SZPT) is excluded** from the SLA.
- Credits: notify support within 30 days with latency logs.

## 4. General Vertex AI Platform SLA — does NOT cover Gemini

- [Vertex AI SLA](https://cloud.google.com/vertex-ai/sla) covers Training / Batch Prediction / AutoML (≥99.9%), Custom Model Online Prediction & Pipelines (≥99.5%). **Gemini / generative models are NOT a Covered Service here** — they have their own SLA (§2 above). Easy to cite the wrong page.
