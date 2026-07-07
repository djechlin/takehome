The original README is moved to `INSTRUCTIONS.md`.

Our goal is to determine whether some of our workloads can run on Gemini 2, and to add evidence by running a load test.

# Usage

```
cp .env.example .env # add the mongodb password
make setup
make start-backend-local
make start-web
```

Then visit `localhost:4454` and run an experiment, or click the `runs` tab to view existing run stats.

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

At parallelism=100, on Monday, some 429s showed up, suggesting this is around the load we can send to the service. On Sunday, I didn't get any 429s.

The P=100,200,300 were run successively, in an attempt to observe degradation at higher levels, but they showed about the same performacne. I'm also running from my own laptop and network and might be hitting a limit locally regarding concurrent connections.

| P | N | Start (PT) | OK / Err | RPS | P50 | Out tok/req | Thinking/req | Out tok/min |
|--:|--:|:-----------|:---------|----:|----:|------------:|-------------:|------------:|
| 100 | 1000 | Sun Jul 5, 17:50 | **999 / 1** | 3.97 | 23.8s | 3550 | 1600 | **845k** | — |
| 100 | 300 | Mon Jul 6, 10:41 | 288 / 12 | 2.77 | 26.9s | 3571 | 1574 | 596k | — |
| 200 | 300 | Mon Jul 6, 10:43 | 295 / 5 | **2.92** | 50.3s | 3590 | 1574 | 631k | — |
| 300 | 300 | Mon Jul 6, 10:45 | 295 / 5 | 2.14 | 52.0s | 3531 | 1569 | 456k | — |

Aside from one spurious connection error, every error is a `429 RESOURCE_EXHAUSTED`. P=100,200,300 gave these errors when run on Monday and not when run on Sunday.