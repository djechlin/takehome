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

Here are the most notable success runs and failure runs. The first row is a
clean **pass** — a Sunday-evening N=1000 run that landed 999/1000 with spare
throughput. The three rows below it are a Monday P-sweep (same N=300 fired at
each P, `run_id 6a4bea102d6bc5c3be90ff1d`, total $7.83) that hits the wall:
P=100/200 hold, P=300 tips past saturation. All rows are web off, Gemini 2.5
Flash, temp=1.0. Start times are Pacific.

|  | P | N | start (PT) | ok/err | rps | p50 | out tok/req | thinking/req | out tok/min |
|:--|--:|--:|:--|:--|--:|--:|--:|--:|--:|
| **pass** | 100 | 1000 | Sun Jul 5, 17:50 | **999 / 1** | 3.97 | 23.8s | 3550 | 1600 | **845k** |
| sweep | 100 | 300 | Mon Jul 6, 10:41 | 288 / 12 | 2.77 | 26.9s | 3571 | 1574 | 596k |
| sweep | 200 | 300 | Mon Jul 6, 10:43 | 295 / 5 | **2.92** | 50.3s | 3590 | 1574 | 631k |
| sweep | 300 | 300 | Mon Jul 6, 10:45 | 295 / 5 | 2.14 | 52.0s | 3531 | 1569 | 456k |

Every error is a `429 RESOURCE_EXHAUSTED` — Vertex Dynamic Shared Quota shedding
load, already binding at P=100 in the sweep (~4% error rate). The Sunday pass
ran the same P=100 with essentially no errors and higher throughput, which is
exactly the weekend-vs-weekday noise this section is about. Within the sweep,
throughput plateaus at ~600–630k output tok/min regardless of concurrency, and
P=300 is past the knee: rps *falls* and the tail blows up. Full analysis, the
tokens/min ceiling, and reproduction steps are in [RESULTS.md](RESULTS.md).