# Load-test findings — Gemini 2.5 Flash on Vertex

What the workload does, where it degrades, and what actually limits throughput.
All numbers are from real runs saved to MongoDB Atlas (`evertune_loadtest.run`)
and are reproducible with `make runs` / the Runs tab.

## TL;DR

- **Throughput is token-bound, not request-bound.** The effective ceiling is
  **~600–700k output tokens/min**, and it holds across two independent
  experiments with very different per-request sizes.
- **The wall is Vertex quota (DSQ), hit as early as P≈100.** Every error is
  `429 RESOURCE_EXHAUSTED` — Vertex shedding load by design, not the model
  falling over.
- **The concurrency knee is P≈150–200.** Past it you get *negative* returns:
  throughput drops and tail latency explodes.
- **~44% of each response is hidden "thinking"** under `thinking_budget=-1`
  (auto). That's the biggest controllable lever on cost and throughput.
- To go faster you **raise DSQ quota or buy Provisioned Throughput** — adding
  client concurrency past the knee makes things worse, not better.

## Workload

- Prompt: `recommend me co-op switch games` (open-ended, token-heavy — the model
  writes a long list).
- `enable_web=false` — no Google Search grounding, so every request goes
  straight down the generation path (hotter on the model).
- `temperature=1.0`, `thinking_budget=-1` (auto), Gemini 2.5 Flash.
- Method: a **P-sweep** — the same N=300 requests fired at each parallelism P in
  turn, so latency/throughput/errors can be read against concurrency. N ≥ P at
  every step, so each step genuinely reaches its stated concurrency.

## The degrade curve

Sweep `run_id 6a4bea102d6bc5c3be90ff1d`, N=300/step, web off, total $7.83.

| P | ok/err | rps | p50 | p95 | p99 | p100 | queue max | out tok/req | thinking/req | tok/min |
|--:|:--|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| 100 | 288 / 12 | 2.77 | 26.9s | 34.6s | 38.0s | 43.3s | 64.2s | 3571 | 1574 | **596k** |
| 200 | 295 / 5  | **2.92** | 50.3s | 62.3s | 71.8s | 83.0s | 34.5s | 3590 | 1574 | **631k** |
| 300 | 295 / 5  | 2.14 | 52.0s | 88.3s | 95.6s | **137.6s** | 0.0s | 3531 | 1569 | 456k |

Reading it:

1. **429s are the failure mode.** 11 of the 12 errors at P=100 (plus one
   `ConnectError`) and all 5 at P=200/300 are `429 RESOURCE_EXHAUSTED`. The wall
   is Vertex **Dynamic Shared Quota**, and it's already binding at P=100
   (~4% error rate). No client-side backoff/retry exists yet — that's a
   production must-have.
2. **Throughput plateaus immediately.** Doubling concurrency 100→200 moved rps
   only 2.77→2.92 (+5%); tok/min held at ~600–630k. We're bouncing off a
   tokens-per-minute budget, not a request-rate limit.
3. **P=300 is past saturation.** rps *falls* to 2.14, tok/min drops to 456k, and
   the tail blows up (p100 43s→138s). Extra concurrency beyond the knee creates
   contention with no capacity to serve it. **Optimal operating point ≈ P=200.**

## The tokens/min ceiling reconciles an earlier run

An earlier standalone run — `good co-op video games for switch`, **web on**,
N=2000 at P=500 — got **10.6 rps** with 0 errors, which naively looks like "it
scales fine to 500." It doesn't contradict the sweep once you normalize to
tokens:

| Run | tok/req | rps | → tokens/min |
|:--|--:|--:|--:|
| web-on, P=500, N=2000 | ~1,090 | 10.6 | **~705k** |
| web-off sweep, P=200, N=300 | ~3,690 | 2.9 | **~631k** |

Same ~600–700k tokens/min ceiling; this query just packs **~3.3× more tokens per
request**, so it delivers ~3.3× fewer requests/sec (10.6 → 2.9). **Requests/sec
is the wrong unit for this model — tokens/min is the invariant.**

## Model quirks worth flagging

- **Thinking is a large, hidden cost.** ~1,570 of ~3,570 output tokens per
  request (44%) are reasoning tokens the caller never sees, billed at the output
  rate. A one-line list request spends heavily on hidden thinking under auto
  budget. Capping `thinking_budget` is the biggest lever on both cost and
  throughput (see next steps).
- **Degradation is quota-shaped, not latency-shaped.** The model doesn't slow
  down gracefully; Vertex returns 429s. You manage this with backoff + concurrency
  caps, not by waiting longer.

## What I'd do next for production

1. **Add 429 backoff + retry** (exponential, jittered). Non-negotiable — 429s
   start at P=100.
2. **Cap client concurrency ~150–200** for this token profile. More is strictly
   worse.
3. **Scale via quota, not concurrency** — request higher DSQ, or buy
   **Provisioned Throughput** for a guaranteed QPS/SLA. For bulk/offline volume,
   **Batch Prediction** runs at ~half price (needs GCS I/O).
4. **Measure the thinking lever** — rerun at P=200 with `thinking_budget=0` and
   compare tok/req, rps, and cost. Expectation: ~2× throughput / ~½ cost if
   answer quality holds. (Not yet run.)

## Reproducing

```
make start-backend-local          # backend on :4460 (ADC auths Vertex)
make runs                         # summarize saved runs from Atlas
# UI: make start-web  ->  http://localhost:4454  (Runs tab; click a row for
# per-request errors + success examples)
```

The P-sweep above was driven through the backend `/sweep` endpoint with
N=300, `p_list="100, 200, 300"`, `enable_web=false`.
