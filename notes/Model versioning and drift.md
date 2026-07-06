# Model versioning, aliases, deprecation

[model-versions](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/model-versions) · [2.5 GA blog](https://cloud.google.com/blog/products/ai-machine-learning/gemini-2-5-flash-lite-flash-pro-ga-vertex-ai)

- **`gemini-2.5-flash` IS the GA stable ID** — there is no separate `-001` dated stable pin for the 2.5 line. GA'd **2025-06-17**. Dated 2.5 variants that exist are *preview* (e.g. `gemini-2.5-flash-preview-09-2025`), not stable pins.
- **The auto-updated (unpinned) alias re-points automatically** when a new latest-stable ships — i.e. **the model can change under you silently.**
- Each stable version has a **retirement date** (hard expiry). Gemini 2.5 Flash / Flash-Lite / Pro retirement = **2026-10-16**. Preview `-preview-09-2025` discontinued **2026-07-09** (→ move to `gemini-2.5-flash`).
- Notice period: governed by ToS "Discontinuation of Services." Preview models retire **45 days** after a replacement ships; stable versions get an announced retirement date + migration guidance.

## 🟠 Concerning for Evertune's drift-tracking workload

Workload #7 (WORKLOADS.md) = re-run brand panels daily and detect when a model's brand associations shift. **A silent alias re-point would masquerade as brand drift** — you couldn't tell a real associations shift from a model swap.

- **Record the resolved model version with every run** (dump it into the Mongo run stats).
- Prefer pinning; but for 2.5, the alias *is* the stable ID, so your only true pin is the stable release — which carries a **hard 2026-10-16 expiry**. Your reproducible baseline has a forced re-baselining / migration event on that date.
- Monitor [release notes](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/release-notes).
