# Batch inference — near-perfect fit for Evertune's offline sweeps

[batch-prediction-gemini](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/batch-prediction-gemini) · [GEAP batch](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/batch-inference)

- **50% off** the online/real-time rate for the same model. (Implicit caching's 90% discount takes precedence and does **not** stack with batch.)
- **Separate capacity pool, no predefined quota** — "large shared pool, dynamically allocated... no predefined quota limits." **Decoupled from your online DSQ**, so daily bulk runs won't compete with / starve interactive traffic. Under saturation, batch jobs **queue** rather than 429.
- **Turnaround: most jobs finish within 24h** after they start running (target, not a hard SLA). A job may sit **in queue up to 72h** before expiring.
- **Size: up to 200,000 requests/job**; Cloud Storage input file ≤ **1 GB**.

## Why this matters here

Evertune's core workloads are **batch, offline, fan-out measurement jobs** (share-of-voice sweeps, ranking extraction, daily drift panels) — not interactive chat. Batch mode fits:
- 50% cheaper on million-prompt sweeps.
- Own pool → won't burn the DSQ shared lane or trip ramp/burst limits.
- 24h turnaround is fine for a **daily** schedule; 200k/job absorbs thousands of prompts.
- **Design around queue variability (up to 72h)**, not a guaranteed completion time.

⚠️ Our current harness is **synchronous online** `ask_generic_question` (the load-test path). Batch is a *different API surface* — worth prototyping separately; it likely changes the "how do we prove scale" story more than any tuning of the online path.
