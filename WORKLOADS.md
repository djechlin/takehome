# Workloads This Load Test Likely Supports

Notes on what this repo is, what Evertune does, and which production workloads the
Gemini provider would carry — so the load test targets the right thing.

## What this project is

An Evertune take-home: **add Gemini 2.5 Flash (on Google Vertex) as a provider to
the LLM-vendor abstraction, and prove it holds up at production scale.**

The abstraction is deliberately thin:

- **`llm/llm.py`** — abstract `LLM` base class. Entire surface:
  - `ask_generic_question(system_prompt, question, temperature) -> SimpleResponse`
    — one-shot, **no chat history/context**.
  - `parallelism()` — concurrent requests the provider tolerates.
  - `SimpleResponse` carries `answer`, `input_tokens`, `output_tokens`, `reasoning_tokens`.
- **`llm/together.py`** — reference provider (Together AI). Requests **`logprobs=1`**
  on every call.
- **`llm/gemini.py`** — the new provider. Folds hidden "thinking" tokens into
  `output_tokens` so cost isn't under-reported (2–4x otherwise).
- **`server.py`** — one-question-at-a-time web app with token/cost/latency analytics.
- **`scripts/`** — smoke test + probes characterizing Gemini thinking-token behavior
  (latency, and that `thinking_budget` is a per-request knob).

The shape — **stateless single-turn Q&A, per-request token accounting, a
`parallelism()` throttle, and logprobs capture** — tells you what production traffic
this serves.

## What Evertune does

A **"brand discovery in AI search" / Generative Engine Optimization (GEO)** platform,
founded 2024 by ex-Trade Desk people. It measures and optimizes **how brands appear
in LLM outputs** (ChatGPT, Gemini, Perplexity, 10+ models). Key point: one of three
data sources is **direct base-model API access** — running large volumes of prompts
against raw LLMs to measure "how AI fundamentally perceives your brand," backed by a
25M-person consumer panel. Their pitch is explicitly **statistically significant**
measurement, not "a handful of manual prompts" — i.e. high prompt volume.

Sources: https://www.evertune.ai/ ·
https://www.evertune.ai/about-us/company-overview ·
https://www.adexchanger.com/marketers/meet-evertune-a-gen-ai-startup-founded-by-trade-desk-vets/

## Likely workloads (what the load test should model)

These are **batch, offline, fan-out measurement jobs** — not interactive chat.

1. **Brand-mention / share-of-voice sweeps (the core one).** Fire the *same* prompt
   (e.g. "What are the best running shoe brands?") thousands of times across models,
   categories, and paraphrases; count which brands surface and how often. Stateless
   `ask_generic_question` + `parallelism()` is exactly this fan-out primitive.
   Statistical significance needs **thousands to millions of completions** — this is
   where scale matters most.
2. **Ranking / recommendation-order extraction.** "Rank the top 10 CRM tools." Parse
   ordered lists to compute average rank per brand across many samples.
3. **Logprob-based probability measurement.** The Together provider's `logprobs=1` is
   the tell: read the **token-level probability** a model names a brand, rather than
   sampling thousands of times. Lower variance, cheaper. NOTE: **Vertex Gemini does
   not expose comparable logprobs** — a real parity gap.
4. **Sentiment / attribute association.** "Describe brand X" / "Is X good for
   beginners?" at volume, to measure how models characterize a brand.
5. **Competitive-intelligence matrices.** brand x attribute x model x prompt-variant
   grids — combinatorial, multiplies request count fast.
6. **Content-strategy testing.** Content Studio tests candidate messaging for
   LLM-friendliness — feed copy through models to see if it lifts recommendation odds.
7. **Continuous monitoring / drift tracking.** Re-run the above panels on a schedule
   (site emphasizes *daily* data) to detect when a model's brand associations shift
   after a new checkpoint.

## Why this matters for the Gemini load test

Binding constraints these workloads impose:

- **High sustained concurrency** against Vertex quotas (not p99 of one interactive request).
- **Cost per completion** — why the thinking-token accounting fix mattered; thinking
  tokens can 2–4x real cost and inflate latency on a million-prompt sweep.
- **Throughput/latency of large batches**, and thinking-token *variance* (the probes
  already dug into this — it's what blows up cost and tail latency at scale).

Open question to verify with the team: whether Gemini's lack of Together-style logprobs
breaks any workload that depends on probability signal rather than sampling — the
sharpest edge between the reference provider and the new one.
