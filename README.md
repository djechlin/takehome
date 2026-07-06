The assignment is to load test Gemini 2.5-flash on Vertex.AI (hereafter, "Gemini").

The original README is moved to `INSTRUCTIONS.md`.

# Load test design

The two most important concerns are modeling our workload, and understanding the resource's SLA. The load test is testing, specifically, that we modeled our workload correctly with respect to the concerns in the SLA.

Load testing a serverless resource can produce both false passes and false failures.

- A "pass" may mean extra resources were available at the time, or the load test was cached really well, or there was no cold start time because the test was steady, etc.
- A "fail" may mean a limit was hit that's not likely in prod, e.g. a ramp limit, or a burst limit.
- Furthermore, the load test can differ from a real workload in many other ways, and the workload load tested may differ from other workloads.

Notably insidiouss: you might even change usage tiers because of the load test! That's how hard it is to faithfully load test a serverless resource.

"Passes" can be totally ambiguous, particularly because they can take place during a service's downtime (in fact, these tests took place over a holiday weekend).

Other strategies include:

- Fork real prod traffic, producing a faithful test workload.
- Migrate just one workload, that's ideally both high-load and not mission-critical or easy to roll back.

# Test workload

I used the workload of taking a real user query and running it many times to see if the desired product shows up.

- Query = "recommend me switch co-op games", which triggers thinking by default, and produces a longer response, and takes 15-20 seconds, so it's fairly expensive.
- Temperature = 1.0, so outputs should be not cached, and the smaller input may have been cached.
- Web search = enabled, since real users have it enabled. This likely makes the test lower-load per-second since more time is spent in search, and this lowers the burstiness of the test.

# Gemini SLAs

It's really important to understand these thoroughly, and hard to verify that one does understand these thoroughly. My strategy is to take thorough notes to the best of my ability, then give the notes to Claude 4.8 and prompt it to find stuff I missed. Furthermore there's a bunch of good advice in 1st-party blogs, and some good advice in 3rd-party blogs.


# Results

Bearing in mind the above, these "passes" are not too high signal, nonetheless they show real workloads succeeded, and they can also be diffed against our GCP bill to make sure that's as expected.



# Application

- I saved run statistics to MongoDB. Once you start spending real money, wall-time or dev-effort testing something, you really don't want to lose your logs. And your logs schema will be changing constantly, so you want to dump them somewhere semi-structured i.e. schemaless JSON, and MongoDB is a good choice for this.
- The backend runs on gcloud to avoid laptop-dependent limits.
- The frontend you run locally because I didn't want to manage auth for a real web endpoint.
- I did *not* attempt to scale my load test beyond one Cloud Run process for these reasons:
  - it gets high effort, as a run becomes a job that fans out to multiple backend nodes.
  - it's not very strategic: surely there's a better way to get the info we need from a load test than using more brute force?
  - More stuff can go wrong: we spend $10000 in 60 seconds, we burn GCP quota for just servicing our non-AI jobs.

## Auth / security

- Resources to protect:
  - the mongodb instance. It's just a free tier with nothing besides our run stats, but I'd rather not trivially expose the creds.
  - Our access to Vertex.AI Gemini, billed to us.
- Don't build any auth besides gcloud user tokens.
- Don't expose the experiment via a web endpoint.
- Don't publish secrets to github.
- However, do publish secrets to gcloud, as a secure-enough workaround to my lack of access to secrets manager.

# Modeling the test workload

It's important to *not* assume that one workload load-testing well, will generalize to other workloads. Our learnings and findings might generalize, but not the simple yes/no result of the test.

This requires picking or designing the workload to load test. If there are multiple options, my preferred criteria are:

1. Has business impact immediately
2. Not mission-critical
3. Moderate complexity: complex enough to be generalizable, simple enough to learn about the service, not just our workload

What I do next is copy down some real workloads and turn them into test workloads, and make sure they're representative of the input tokens, output tokens, and latency we see for our existing workloads.

For this exercise, I designed the test workload as follows:

1. Use the workload where we try a query 100 times against Gemini and try to see what products come up. So a single "workload" requires 100 queries.
2. Set it to dynamic thinking, with access to Google search, since this best approximates what users do for real.
3. Pick a query that gives a list output, exercising both thinking and Google search.

# Harness

I wrote the harness as a personal-use webapp, because agents can write those quickly, and I wanted to explore interactively.

I dumped results to MongoDB which is a good database for early logs exploration.

# VertexAI limits (quota & DSQ)

VertexAI serves 2.5-flash under **DSQ (Dynamic Shared Quota)**: instead of a fixed per-project QPM/TPM you request and manage, all pay-as-you-go traffic for the model draws from one large **shared pool of serving capacity per region**, allocated in real time based on total supply and total demand across *all* customers of that model. Few customers active → you can burst high; many active → your share shrinks. There's no per-project number to stay under.

## How DSQ works (the two lanes)

It's not "unlimited until it randomly breaks." Your org has a default **tokens-per-second (TPS) threshold** that splits traffic into two priority lanes:

1. **Within threshold → high priority, ~99.5% SLO.** This is the "good SLA" fast lane.
2. **Above threshold → low priority, best-effort.** Excess requests get throughput only when the shared pool has spare capacity, and otherwise come back as **429 `RESOURCE_EXHAUSTED`**. This is the "shaky SLA" slow lane, and where load-shedding happens under contention.

So the knobs that matter are: stay under the TPS threshold to keep the 99.5% lane, and treat the 429 rate above it as a load-dependent (not fixed) signal. **Provisioned Throughput** is the escape hatch — reserve GSUs (Generative AI Scale Units) for a guaranteed `GSUs × throughput/GSU` ceiling served from dedicated capacity, isolated from the shared pool. Fixed quota = a reserved parking space; DSQ = a big shared lot; PT = a rented private garage. ([DSQ docs](https://cloud.google.com/vertex-ai/generative-ai/docs/resources/dynamic-shared-quota), [PT docs](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/provisioned-throughput/use-provisioned-throughput))

The project's quota API shows no fixed limit set for 2.5-flash (`scripts/quota_report.py`), consistent with DSQ. Empirically, a burst of **N=20 at P=10 returned 20/20 with zero 429s** (p50 2.9 s, p95 7.4 s, ~$0.0012/request) — comfortable headroom in the fast lane at small concurrency.

## Why this is hard to test

DSQ is exactly why "just load test it" doesn't give a clean answer:

1. **No deterministic knee.** Under a fixed quota you'd sweep P and watch it fall off a cliff at the limit. Under DSQ, 429s appear based on *Google's* shared-pool load at that moment, not purely on our P. Absence of 429s at high P doesn't prove headroom — the pool may just have been quiet when we tested.
2. **We hit our own ceiling first.** From a laptop the single event loop / one core caps us before we can push Vertex into its slow lane, so the sweep mostly characterizes our client, not Vertex. Testing this for real means moving the load generator in-region and multi-process.
3. **Not reproducible.** The same test at 3am vs. peak could give different 429 rates. The result is time-dependent.

So the honest outcome so far is "I failed to DOS Vertex, it performed well under load" — but that's weak evidence, because we can't distinguish "genuinely has headroom" from "the shared pool happened to be quiet."

## What this means for production

If we need a capacity *guarantee*, the answer is **Provisioned Throughput** — reserve dedicated capacity (priced per GSU), which converts DSQ's best-effort into a committed SLA. Otherwise we design for DSQ: retry 429s with exponential backoff + jitter, cap concurrency to stay in the fast lane, and **monitor the 429 rate as the real production signal** rather than trusting a one-off load test. The number that matters in prod isn't a QPM we picked — it's "what fraction of requests get throttled at our actual traffic, and does our backoff keep tail latency acceptable."



Fundamental law of migrations: if one workload migrates, don't assume the others will also migrate.

Key point: we can't assume the

I focused on the workload:

1. Approximate a user's web AI-chat environment: dynamic thinking, with Google se

Early in testing, I ran into this example:

- "What is the capital of France?" -> "Paris", 4 seconds
- "Recommend me some co-op Switch games" -> a long list response, 17 seconds

Even within one hypothetical workload - answer user questions - subtle differences within that workload produce very different load outcomes. 

What workload we're testing matters a lot. That one workload scales, does not necessarily indicate others will.

Gemini/LLM work loads are extremely varied. Even if the workload is "answer one-off user questions," I've found that

It's important to pick a first workload to verify production readiness. 

In a real project, I would prioritize load testing the highest priority workload.

1. What is the first workload I'm supporting? and I would model and load test that workload.
2. Am I building general team know-how for working with this model, for more general workloads?



Workloads can degrade these ways:

1. More failures, probably 429s
2. Higher latency
3. Physically scales fine, but costs too much USD
4. 

There's two approaches:

1. Have some formal understanding of Gemini's SLA and be able to describe what workloads will work.
2. 

One simplistic answer is, "I failed to DOS Vertex.AI. It performed well under load."




 At a glance, that's a strange task: Vertex.AI is a mature, serverless resource, so one likely outcome is we load test our own credit card limit.

The real question is "Can we use Gemini? I'm worried it'll break under load". Ways it might "break" include:

1. 