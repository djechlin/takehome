The original README is moved to `INSTRUCTIONS.md`.

Our goal is to determine whether some of our workloads can run on Gemini 2, and to add evidence by running a load test.

# Gemini service overview

A brief overview of Gemini's SLOs:

- we are using Standard PayGo, which has a 99.5% SLO within our usage tier, and a best-effort SLO after. We move up usage tiers automatically per month - in fact, a load test itself might cause a change in usage tiers.
  - ⚠️ This means if our load test runs off-hours, "best effort" may simply mean the resource is at 100% uptime, not that the load test would pass at normal business hours.
- Ramp limits and burst limits both exist. A load test that runs without a 10 minute warmup may simply trigger the ramp limit, and GCP recommends we spread our load out evenly over each minute. It can be hard to tune a load test to match production traffic with respect to limits like these.

Also: we are not *really* load testing Gemini; we're testing that our workloads behave as expected on Gemini under load. This requires modeling a real workload. For this, I use the workload where we run a real user query in ChatGPT or similar asking for product recs, and I use an expensive query like "recommend me video games" that triggers thinking. I used temp = 1.0 which should bust output token caching, and tested both with web search on/off, with "on" being more realistic for the prod workload.


# Application design

- I saved run stats to MongoDB.
- I explored running from Cloud Run to avoid laptop-environment indeterminism but don't have access, and I don't think it mattered for the loads I was testing at. I worked out the auth challenges where the frontend runs locally and auths from `application-default` and talks to the Cloud Run backend, but that's not needed.
- I didn't try scaling the load test itself. I wouldn't explore this until we consider strategies like forking prod traffic.

# Results

The results are noisy. All I really proved is: sometimes I can run a lot of of queries successfully, and other times I get 429s, which suggest that the load test I'm running is roughly on the same order of magnitude as the service availability for our tier.

The successes might be caused by running the tests on the weekend, when there are spare resources. Or, the failures might be caused by running enough tests in a row that they were not actually hermetic, and were using each other's ramp limits or rate limits over a longer period.

Here are the most notable success runs and failure runs. This P-sweep (same
N=300 requests fired at each parallelism P, web off, Gemini 2.5 Flash, total
$7.83, `run_id 6a4bea102d6bc5c3be90ff1d`) captures both in one experiment:
P=100/200 succeed, while P=300 tips past saturation.

| P | ok/err | rps | p50 | p95 | p99 | p100 | queue max | out tok/req | thinking/req | tok/min |
|--:|:--|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| 100 | 288 / 12 | 2.77 | 26.9s | 34.6s | 38.0s | 43.3s | 64.2s | 3571 | 1574 | **596k** |
| 200 | 295 / 5  | **2.92** | 50.3s | 62.3s | 71.8s | 83.0s | 34.5s | 3590 | 1574 | **631k** |
| 300 | 295 / 5  | 2.14 | 52.0s | 88.3s | 95.6s | **137.6s** | 0.0s | 3531 | 1569 | 456k |

Every error is a `429 RESOURCE_EXHAUSTED` — Vertex Dynamic Shared Quota shedding
load, already binding at P=100 (~4% error rate). Throughput plateaus at
~600–630k output tok/min regardless of concurrency, and P=300 is past the knee:
rps *falls* and the tail blows up (p100 43s → 138s). Full analysis, the
tokens/min ceiling, and reproduction steps are in [RESULTS.md](RESULTS.md).