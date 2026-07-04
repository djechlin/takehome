"""Load-test console for the Gemini provider.

You set a total request count N and a parallelism factor P, plus the model knobs
(temperature, thinking budget, web grounding, logprobs). The server fires N
copies of the SAME query, pushing them through an asyncio.Semaphore(P) so at
most P are in flight at once — a queue you can widen to hunt for the P where
Vertex starts to degrade (latency climbs, errors appear, throughput plateaus).

Every run reports wall time, throughput, latency percentiles (p50/p95/p99/p100),
per-request queue wait, cost, and the distinct answers with frequencies (the
"which brands, how often" recall view). Each run is capped at $10 of spend and
saved in full to a local MongoDB (`evertune_loadtest.run`).

Run:  python3 server.py     ->  http://localhost:4454
"""
import asyncio
import glob
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from llm import Gemini, try_save_run, try_recent_runs

PORT = 4454
llm = Gemini()

# Approximate Gemini 2.5 Flash list price (USD per 1M tokens). Thinking tokens
# bill at the output rate and are already folded into output_tokens. Override
# via env if prices change or a different model is used.
PRICE_IN_PER_M = float(os.getenv("GEMINI_PRICE_INPUT_PER_M", "0.30"))
PRICE_OUT_PER_M = float(os.getenv("GEMINI_PRICE_OUTPUT_PER_M", "2.50"))

# Guardrails. N and P are bounded so a stray "100000" can't melt the quota or the
# box; every run also self-aborts once spend crosses MAX_RUN_COST.
MAX_SAMPLES = int(os.getenv("GEMINI_MAX_SAMPLES", "2000"))
MAX_PARALLELISM = int(os.getenv("GEMINI_MAX_PARALLELISM", "500"))
MAX_RUN_COST = float(os.getenv("GEMINI_MAX_RUN_COST", "10.0"))

# Run ONE event loop for the whole process, in a background thread. The
# google-genai async client's httpx pool binds to the loop it's first used on;
# using asyncio.run() per request would create and close a new loop each time,
# so the second request would hit "RuntimeError: Event loop is closed". Each
# request thread dispatches its coroutine onto this shared loop and blocks.
_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True).start()


def run_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, _loop).result()


def _cost(in_tok, out_tok):
    return in_tok / 1e6 * PRICE_IN_PER_M + out_tok / 1e6 * PRICE_OUT_PER_M


async def run_batch(params, n, p, cost_cap):
    """Push N identical requests through a width-P queue.

    Returns (records, wall_ms, capped). `capped` is True if the run hit the
    spend cap and skipped the remaining queued requests. Timing is monotonic:
      wait_ms    = time spent queued waiting for a semaphore slot
      service_ms = time from acquiring the slot to the response (the real
                   latency the model+network cost us at this P)
    """
    sem = asyncio.Semaphore(p)
    lock = asyncio.Lock()
    state = {"cost": 0.0, "capped": False}
    t0 = time.perf_counter()

    def ms(dt):
        return int(dt * 1000)

    async def one(i):
        submit = time.perf_counter()
        # Cheap pre-check: if we're already capped, don't even queue.
        if state["capped"]:
            return {"ok": False, "skipped": True, "index": i}
        async with sem:
            if state["capped"]:
                return {"ok": False, "skipped": True, "index": i}
            acquired = time.perf_counter()
            try:
                resp = await llm.ask_generic_question(**params)
                done = time.perf_counter()
                cost = _cost(resp.input_tokens, resp.output_tokens)
                async with lock:
                    state["cost"] += cost
                    if state["cost"] >= cost_cap:
                        state["capped"] = True
                return {
                    "ok": True,
                    "index": i,
                    "answer": resp.answer,
                    "input": resp.input_tokens,
                    "output": resp.output_tokens,
                    "reasoning": resp.reasoning_tokens,
                    "cost": cost,
                    "grounded": resp.grounded,
                    "sources": list(resp.sources),
                    "search_queries": list(resp.search_queries),
                    "avg_logprob": resp.avg_logprob,
                    "wait_ms": ms(acquired - submit),
                    "service_ms": ms(done - acquired),
                    "start_offset_ms": ms(acquired - t0),
                }
            except Exception as e:
                done = time.perf_counter()
                return {
                    "ok": False,
                    "index": i,
                    "error": f"{type(e).__name__}: {e}",
                    "wait_ms": ms(acquired - submit),
                    "service_ms": ms(done - acquired),
                    "start_offset_ms": ms(acquired - t0),
                }

    records = await asyncio.gather(*[one(i) for i in range(n)])
    wall_ms = ms(time.perf_counter() - t0)
    return records, wall_ms, state["capped"]


async def run_sweep(params, n, p_list, total_budget):
    """Run the same N-request batch at each P in p_list, one P at a time, so we
    can see how latency/throughput/errors change as concurrency climbs. The
    whole sweep shares a single spend budget and stops early once it's used up.
    Returns (steps, spent) where each step is an aggregate plus its P."""
    steps = []
    spent = 0.0
    for p in p_list:
        remaining = total_budget - spent
        if remaining <= 0:
            steps.append({"parallelism": p, "step_skipped": True})
            continue
        records, wall_ms, capped = await run_batch(params, n, p, remaining)
        agg = aggregate(records, wall_ms, p, capped)
        spent += agg["cost"]["total"]
        # Drop the full per-request array from the sweep payload — one row per P
        # is what the curve needs, and it keeps the saved doc small.
        agg.pop("distinct", None)
        steps.append(agg)
    return steps, spent


def _pct(sorted_vals, p):
    if not sorted_vals:
        return 0
    i = min(len(sorted_vals) - 1, int(round((p / 100) * (len(sorted_vals) - 1))))
    return sorted_vals[i]


def aggregate(records, wall_ms, p, capped):
    """Roll per-request records into the load-test numbers the UI shows."""
    ok = [r for r in records if r["ok"]]
    errors = [r["error"] for r in records if not r["ok"] and not r.get("skipped")]
    skipped = sum(1 for r in records if r.get("skipped"))

    in_tok = sum(r["input"] for r in ok)
    out_tok = sum(r["output"] for r in ok)
    reasoning = sum(r["reasoning"] for r in ok)
    cost_total = sum(r["cost"] for r in ok)
    n_ok = len(ok) or 1

    service = sorted(r["service_ms"] for r in ok)
    waits = sorted(r["wait_ms"] for r in ok)
    throughput = (len(ok) / (wall_ms / 1000)) if wall_ms else 0

    # Recall view: distinct answers (whitespace-normalized) by frequency.
    counts = {}
    for r in ok:
        key = " ".join((r["answer"] or "").split())
        counts[key] = counts.get(key, 0) + 1
    distinct = sorted(
        ({"answer": a, "count": c} for a, c in counts.items()),
        key=lambda d: -d["count"],
    )

    logprobs = [r["avg_logprob"] for r in ok if r["avg_logprob"] is not None]
    sources = sorted({s for r in ok for s in r["sources"]})
    queries = sorted({q for r in ok for q in r["search_queries"]})

    return {
        "parallelism": p,
        "requested": len(records),
        "ok": len(ok),
        "errors": errors,
        "error_count": len(errors),
        "skipped": skipped,
        "capped": capped,
        "wall_ms": wall_ms,
        "throughput_rps": round(throughput, 2),
        "grounded": any(r["grounded"] for r in ok),
        "distinct": distinct,
        "sources": sources,
        "search_queries": queries,
        "avg_logprob": (sum(logprobs) / len(logprobs)) if logprobs else None,
        "latency_ms": {
            "p50": _pct(service, 50), "p95": _pct(service, 95),
            "p99": _pct(service, 99), "p100": service[-1] if service else 0,
        },
        "queue_ms": {"p50": _pct(waits, 50), "p100": waits[-1] if waits else 0},
        "usage": {
            "input": in_tok, "reasoning": reasoning,
            "answer": out_tok - reasoning, "output": out_tok,
            "total": in_tok + out_tok,
        },
        "cost": {
            "total": cost_total,
            "per_request": cost_total / n_ok,
            "per_1k": cost_total / n_ok * 1000,
        },
        "price": {"input_per_m": PRICE_IN_PER_M, "output_per_m": PRICE_OUT_PER_M},
    }


PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Gemini 2.5 Flash — load-test console</title>
<style>
  body { font: 15px/1.5 -apple-system, system-ui, sans-serif; max-width: 820px;
         margin: 40px auto; padding: 0 16px; color: #1a1a1a; }
  h1 { font-size: 18px; }
  label { display: block; margin: 14px 0 4px; font-weight: 600; font-size: 13px; }
  textarea, input, select { width: 100%; box-sizing: border-box; padding: 8px;
                    font: inherit; border: 1px solid #ccc; border-radius: 6px; }
  textarea { resize: vertical; }
  .row { display: flex; gap: 14px; flex-wrap: wrap; }
  .row > div { flex: 1; min-width: 120px; }
  .check { display: flex; align-items: center; gap: 8px; margin-top: 22px; }
  .check input { width: auto; }
  .check label { margin: 0; }
  .hint { color: #aaa; font-size: 11px; margin-top: 4px; }
  .intro { color: #555; font-size: 13px; margin: 4px 0 8px; }
  .intro b { color: #1a1a1a; }
  .caption { color: #888; font-size: 11px; margin-top: 6px; line-height: 1.5; }
  .caption b { color: #555; }
  abbr { text-decoration: none; border-bottom: 1px dotted #bbb; cursor: help; }
  button { margin-top: 16px; padding: 9px 18px; font: inherit; font-weight: 600;
           border: 0; border-radius: 6px; background: #1a1a1a; color: #fff; cursor: pointer; }
  button:disabled { opacity: .5; cursor: default; }
  #panel, #sweeppanel { display: none; margin-top: 22px; border: 1px solid #e3e3e3;
           border-radius: 10px; padding: 14px 16px; background: #fafafa; }
  #panel.show, #sweeppanel.show { display: block; }
  .tiles { display: flex; gap: 24px; flex-wrap: wrap; }
  .tile .n { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }
  .tile .k { font-size: 11px; color: #777; text-transform: uppercase; letter-spacing: .04em; }
  .detail { margin-top: 12px; padding-top: 12px; border-top: 1px solid #ececec;
            font-size: 12px; color: #555; display: flex; gap: 18px; flex-wrap: wrap; }
  .detail b { color: #1a1a1a; font-variant-numeric: tabular-nums; font-weight: 600; }
  .banner { margin-top: 12px; padding: 8px 12px; border-radius: 6px; font-size: 12px; }
  .banner.cap { background: #fff4e5; color: #8a5300; }
  .banner.err { background: #fdecef; color: #b00020; }
  .distinct { margin-top: 14px; padding-top: 12px; border-top: 1px solid #ececec; }
  .distinct h3 { font-size: 11px; color: #777; text-transform: uppercase;
                 letter-spacing: .04em; margin: 0 0 8px; }
  .da { display: flex; gap: 10px; align-items: baseline; padding: 5px 0;
        border-bottom: 1px dashed #eee; }
  .da .c { font-variant-numeric: tabular-nums; font-weight: 700; min-width: 46px; }
  .da .bar { height: 6px; background: #1a1a1a; border-radius: 3px; }
  .da .txt { flex: 1; white-space: pre-wrap; font-size: 13px; }
  .meta { margin-top: 12px; font-size: 12px; color: #555; }
  .meta code { background: #eee; padding: 1px 5px; border-radius: 4px; }
  .runid { color: #aaa; font-size: 11px; margin-top: 8px; }
  .actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 16px; }
  .actions .or { color: #888; font-size: 13px; }
  .actions input { max-width: 200px; margin: 0; }
  .actions button { margin-top: 0; }
  #sweepbtn { background: #234; }
  table { width: 100%; border-collapse: collapse; margin-top: 6px; font-size: 13px; }
  th, td { text-align: right; padding: 6px 8px; border-bottom: 1px solid #eee;
           font-variant-numeric: tabular-nums; white-space: nowrap; }
  th:first-child, td:first-child { text-align: left; }
  th { font-size: 11px; color: #777; text-transform: uppercase; letter-spacing: .03em; }
  tr.best td { background: #eef7ee; }
  tr.haserr td { color: #b00020; }
  .sect-h { font-size: 13px; font-weight: 700; margin: 26px 0 2px; display: flex;
            align-items: center; gap: 10px; }
  .sect-h button { margin: 0; padding: 4px 10px; font-size: 12px; background: #666; }
  .empty { color: #aaa; font-size: 12px; margin-top: 6px; }
  .tabs { display: flex; gap: 4px; border-bottom: 1px solid #e3e3e3; margin: 18px 0 4px; }
  .tab { margin: 0; padding: 8px 16px; font: inherit; font-weight: 600; font-size: 14px;
         background: none; color: #888; border: 0; border-bottom: 2px solid transparent;
         border-radius: 0; cursor: pointer; }
  .tab.active { color: #1a1a1a; border-bottom-color: #1a1a1a; }
</style>
</head>
<body>
  <h1>Gemini 2.5 Flash (Vertex) — load-test console</h1>
  <p class="intro">
    Fires the <b>same query N times</b> at Gemini on Vertex, running at most
    <b>P at once</b> (a queue you widen to find where Vertex starts to degrade).
    Reports latency percentiles, throughput, cost, and which distinct answers
    came back. Each run is capped at <b>$10</b> of spend and saved to MongoDB.
  </p>

  <div class="tabs">
    <button class="tab active" id="tabbtn-run" onclick="showTab('run')">Load test</button>
    <button class="tab" id="tabbtn-runs" onclick="showTab('runs')">Runs</button>
  </div>

  <div id="tab-run">
  <label>System prompt</label>
  <input id="sys" value="You are a helpful assistant.">
  <label>Question</label>
  <textarea id="q" rows="3">What are the best running shoe brands? Answer with a short list.</textarea>

  <div class="row">
    <div>
      <label>N — total requests</label>
      <input id="n" type="number" step="1" min="1" value="50">
      <div class="hint">how many times to run the query</div>
    </div>
    <div>
      <label>P — parallelism</label>
      <input id="p" type="number" step="1" min="1" value="10">
      <div class="hint">max requests in flight at once</div>
    </div>
    <div>
      <label>Temperature</label>
      <input id="temp" type="number" step="0.1" min="0" max="2" value="1.0">
      <div class="hint">higher = more varied answers</div>
    </div>
    <div>
      <label>Thinking budget</label>
      <input id="think" type="number" step="1" min="-1" value="-1">
      <div class="hint">reasoning tokens: -1 auto · 0 off · N cap</div>
    </div>
  </div>
  <div class="row">
    <div class="check">
      <input id="web" type="checkbox">
      <label for="web" title="Attaches Google Search. Switches from the model's built-in knowledge to the live-retrieval path real chat apps use.">Enable web (Google Search grounding)</label>
    </div>
    <div class="check">
      <input id="lp" type="checkbox">
      <label for="lp" title="Ask Vertex to return token log-probabilities. Support is model-dependent; if unsupported you'll see the error rather than a silent skip.">Request logprobs (top-5)</label>
    </div>
  </div>

  <div class="actions">
    <button id="go" onclick="ask()">Run once — N at P</button>
    <span class="or">or sweep concurrency →</span>
    <input id="plist" value="1, 5, 10, 25, 50" title="Comma-separated P values.">
    <button id="sweepbtn" onclick="sweep()">Run P-sweep</button>
  </div>
  <div class="hint">Sweep runs N requests at <b>each</b> P in turn and tables
    latency / throughput / errors vs P — that's how you find where Vertex starts
    to degrade. The whole sweep shares the one $10 cap.</div>

  <!-- Single-run results -->
  <div id="panel">
    <div class="tiles">
      <div class="tile" title="Total time to finish all N requests at parallelism P."><div class="n" id="t-wall">–</div><div class="k">wall time</div></div>
      <div class="tile" title="Successful requests per second = ok / wall time. Watch this plateau as P rises."><div class="n" id="t-rps">–</div><div class="k">throughput</div></div>
      <div class="tile" title="Per-request time from acquiring a slot to the response (excludes queue wait). p100 = slowest request. Rising tail is the degrade signal."><div class="n" id="t-lat">–</div><div class="k">latency p50/p95/p100</div></div>
      <div class="tile" title="Total spend for this run, across all successful requests."><div class="n" id="t-cost">–</div><div class="k">cost</div></div>
      <div class="tile" title="Unique answers among successful requests. Same query N times, so more distinct = wider spread of what the model names."><div class="n" id="t-uniq">–</div><div class="k">distinct / ok</div></div>
    </div>
    <div class="detail">
      <span title="Parallelism actually used for this run.">P <b id="d-p">–</b></span>
      <span title="Successful requests out of requested.">ok <b id="d-ok">–</b></span>
      <span title="Requests that returned an error (e.g. 429 rate limit).">errors <b id="d-err">–</b></span>
      <span title="Requests never sent because the $10 cap was hit first.">skipped <b id="d-skip">–</b></span>
      <span title="99th-percentile latency.">lat p99 <b id="d-p99">–</b></span>
      <span title="Time requests spent waiting for a free slot (p50 / max). High values mean P is the bottleneck, not the model.">queue p50/max <b id="d-queue">–</b></span>
      <span title="Total hidden reasoning tokens spent before answers.">thinking <b id="d-think">–</b></span>
      <span title="Whether any request actually used web search.">grounded <b id="d-grounded">–</b></span>
      <span title="Mean log-probability of chosen tokens, if logprobs were returned.">avg logprob <b id="d-logprob">–</b></span>
      <span title="Projected cost to run this query 1,000 times.">$/1k req <b id="d-per1k">–</b></span>
    </div>
    <p class="caption">
      <b>Latency</b> is model+network time per request; <b>queue</b> is time spent
      waiting for one of the P slots. To find the degrade point, raise P and watch
      p95/p100 climb, throughput flatten, or errors appear.
    </p>
    <div id="banner"></div>
    <div class="distinct">
      <h3>Distinct answers (recall)</h3>
      <p class="caption">The same query ran N times — these are the unique responses
        and how often each came back. Wider spread = the model draws from more options.</p>
      <div id="distinct"></div>
    </div>
    <div class="meta" id="meta"></div>
    <div class="runid" id="runid"></div>
  </div>

  <!-- P-sweep results -->
  <div id="sweeppanel">
    <div class="sect-h">P-sweep — latency &amp; throughput vs concurrency</div>
    <p class="caption">Each row is N requests run at that P. Read down the columns:
      when <b>p95/p100 climb</b> or <b>errors</b> appear while <b>throughput</b>
      stops rising, that P is the degrade point. Best-throughput row is highlighted.</p>
    <table id="sweeptable"></table>
    <div class="runid" id="sweepid"></div>
  </div>
  </div><!-- /tab-run -->

  <div id="tab-runs" hidden>
    <div class="sect-h">All runs — one row per run
      <button onclick="loadRuns()">refresh</button>
      <span class="or" style="font-weight:400">summary stats from MongoDB · evertune_loadtest.run</span>
    </div>
    <p class="caption">Each row summarizes one saved run or sweep (not the full
      per-request dump). Newest first. Sweeps show their P list; latency columns
      are the single-run values.</p>
    <div style="overflow-x:auto"><table id="runstable"></table></div>
    <div class="empty" id="runsempty"></div>
  </div>

<script>
const $ = id => document.getElementById(id);
const usd = (n, p = 2) => '$' + (n < 0.01 ? n.toPrecision(p) : n.toFixed(p));
const esc = s => { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; };
const secs = ms => (ms / 1000).toFixed(1) + ' s';
const secD = v => (v == null ? '–' : secs(v));  // null-safe, for table cells

async function ask() {
  $('go').disabled = true;
  $('go').textContent = 'running…';
  $('panel').className = '';
  try {
    const r = await fetch('/ask', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        system_prompt: $('sys').value,
        question: $('q').value,
        temperature: parseFloat($('temp').value),
        thinking_budget: parseInt($('think').value, 10),
        n: parseInt($('n').value, 10),
        p: parseInt($('p').value, 10),
        enable_web: $('web').checked,
        logprobs: $('lp').checked,
      })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'request failed');
    render(d);
    loadRuns();
  } catch (e) {
    $('panel').className = 'show';
    $('banner').innerHTML = '<div class="banner err">' + esc(e.message) + '</div>';
  } finally {
    $('go').disabled = false;
    $('go').textContent = 'Run load test';
  }
}

function render(d) {
  const u = d.usage, c = d.cost, l = d.latency_ms;
  $('t-wall').textContent = secs(d.wall_ms);
  $('t-rps').textContent = d.throughput_rps + ' rps';
  $('t-lat').textContent = secs(l.p50) + ' / ' + secs(l.p95) + ' / ' + secs(l.p100);
  $('t-cost').textContent = usd(c.total, 2);
  $('t-uniq').textContent = d.distinct.length + ' / ' + d.ok;
  $('d-p').textContent = d.parallelism;
  $('d-ok').textContent = d.ok + ' / ' + d.requested;
  $('d-err').textContent = d.error_count;
  $('d-skip').textContent = d.skipped;
  $('d-p99').textContent = secs(l.p99);
  $('d-queue').textContent = secs(d.queue_ms.p50) + ' / ' + secs(d.queue_ms.p100);
  $('d-think').textContent = u.reasoning.toLocaleString();
  $('d-grounded').textContent = d.grounded ? 'yes' : 'no';
  $('d-logprob').textContent = d.avg_logprob === null ? '–' : d.avg_logprob.toFixed(3);
  $('d-per1k').textContent = usd(c.per_1k, 2);

  let banner = '';
  if (d.capped) banner += '<div class="banner cap">Run hit the $' + d.cost_cap +
    ' spend cap — remaining requests skipped.</div>';
  if (d.error_count) banner += '<div class="banner err">' + esc(d.errors[0]) +
    (d.error_count > 1 ? ' (+' + (d.error_count - 1) + ' more)' : '') + '</div>';
  $('banner').innerHTML = banner;

  const max = d.distinct.reduce((m, x) => Math.max(m, x.count), 1);
  $('distinct').innerHTML = d.distinct.map(x =>
    '<div class="da"><span class="c">' + x.count + '×</span>' +
    '<span class="bar" style="width:' + (140 * x.count / max) + 'px"></span>' +
    '<span class="txt">' + esc(x.answer || '(empty)') + '</span></div>'
  ).join('') || '<span class="banner err">no successful samples</span>';

  let meta = '';
  if (d.search_queries.length)
    meta += '<div>searched: ' + d.search_queries.map(q => '<code>' + esc(q) + '</code>').join(' ') + '</div>';
  if (d.sources.length)
    meta += '<div>sources: ' + d.sources.slice(0, 12).map(esc).join(' · ') + '</div>';
  meta += '<div>list price $' + d.price.input_per_m + '/1M in · $' + d.price.output_per_m +
    '/1M out (approx) · ' + usd(c.per_request, 4) + '/request</div>';
  $('meta').innerHTML = meta;

  $('runid').textContent = d.run_id
    ? ('saved to MongoDB evertune_loadtest.run · _id ' + d.run_id)
    : ('not saved to MongoDB: ' + (d.persist_error || '?'));
  $('panel').className = 'show';
}

function body() {
  return {
    system_prompt: $('sys').value,
    question: $('q').value,
    temperature: parseFloat($('temp').value),
    thinking_budget: parseInt($('think').value, 10),
    n: parseInt($('n').value, 10),
    p: parseInt($('p').value, 10),
    enable_web: $('web').checked,
    logprobs: $('lp').checked,
  };
}

async function sweep() {
  $('sweepbtn').disabled = true;
  $('sweepbtn').textContent = 'sweeping…';
  $('sweeppanel').className = '';
  try {
    const r = await fetch('/sweep', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...body(), p_list: $('plist').value})
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'sweep failed');
    renderSweep(d);
    loadRuns();
  } catch (e) {
    $('sweeppanel').className = 'show';
    $('sweeptable').innerHTML = '<tr><td class="haserr">' + esc(e.message) + '</td></tr>';
  } finally {
    $('sweepbtn').disabled = false;
    $('sweepbtn').textContent = 'Run P-sweep';
  }
}

function renderSweep(d) {
  const best = d.steps.reduce((m, s) => Math.max(m, s.throughput_rps || 0), 0);
  const head = ['P', 'ok/err', 'skipped', 'rps', 'p50', 'p95', 'p100', 'queue max', 'thinking', 'cost']
    .map(h => '<th>' + h + '</th>').join('');
  const rows = d.steps.map(s => {
    if (s.step_skipped)
      return '<tr class="haserr"><td>' + s.parallelism +
             '</td><td colspan="9">skipped — $' + d.cost_cap + ' cap reached</td></tr>';
    const l = s.latency_ms, cls = [];
    if ((s.throughput_rps || 0) === best && best > 0) cls.push('best');
    if (s.error_count) cls.push('haserr');
    return '<tr class="' + cls.join(' ') + '">' +
      '<td>' + s.parallelism + '</td>' +
      '<td>' + s.ok + ' / ' + s.error_count + '</td>' +
      '<td>' + s.skipped + '</td>' +
      '<td><b>' + s.throughput_rps + '</b></td>' +
      '<td>' + secs(l.p50) + '</td><td>' + secs(l.p95) + '</td><td>' + secs(l.p100) + '</td>' +
      '<td>' + secs(s.queue_ms.p100) + '</td>' +
      '<td>' + s.usage.reasoning.toLocaleString() + '</td>' +
      '<td>' + usd(s.cost.total, 3) + '</td></tr>';
  }).join('');
  $('sweeptable').innerHTML = '<tr>' + head + '</tr>' + rows;
  $('sweepid').textContent =
    'N=' + d.n + ' per step · total ' + usd(d.total_cost, 3) +
    (d.run_id ? ' · saved _id ' + d.run_id : ' · not saved: ' + (d.persist_error || '?'));
  $('sweeppanel').className = 'show';
}

async function loadRuns() {
  try {
    const r = await fetch('/api/runs');
    const d = await r.json();
    const rows = d.runs || [];
    if (!rows.length) {
      $('runstable').innerHTML = '';
      $('runsempty').textContent = d.error ? ('MongoDB: ' + d.error) : 'No runs saved yet.';
      return;
    }
    $('runsempty').textContent = '';
    const head = ['when', 'type', 'question', 'N', 'P', 'temp', 'web',
                  'ok/err', 'rps', 'p50', 'p95', 'p100', 'wall', 'cost']
      .map(h => '<th>' + h + '</th>').join('');
    const body = rows.map(x => {
      const when = x.created_at ? x.created_at.replace('T', ' ').slice(5, 16) : '–';
      const p = Array.isArray(x.p) ? x.p.join('/') : (x.p ?? '–');
      const okerr = (x.ok ?? '–') + ' / ' + (x.errors ?? '–');
      const wall = x.wall_ms != null ? (x.wall_ms / 1000).toFixed(1) + 's' : '–';
      return '<tr' + (x.errors ? ' class="haserr"' : '') + '>' +
        '<td>' + when + '</td>' +
        '<td>' + x.type + '</td>' +
        '<td style="max-width:200px;overflow:hidden;text-overflow:ellipsis" title="' + esc(x.question || '') + '">' + esc(x.question || '') + '</td>' +
        '<td>' + (x.n ?? '–') + '</td>' +
        '<td>' + p + '</td>' +
        '<td>' + (x.temperature ?? '–') + '</td>' +
        '<td>' + (x.web ? 'yes' : '–') + '</td>' +
        '<td>' + okerr + '</td>' +
        '<td>' + (x.throughput_rps ?? '–') + '</td>' +
        '<td>' + secD(x.p50) + '</td>' +
        '<td>' + secD(x.p95) + '</td>' +
        '<td>' + secD(x.p100) + '</td>' +
        '<td>' + wall + '</td>' +
        '<td>' + (x.cost != null ? usd(x.cost, 3) : '–') + '</td></tr>';
    }).join('');
    $('runstable').innerHTML = '<tr>' + head + '</tr>' + body;
  } catch (e) {
    $('runsempty').textContent = 'Could not load runs: ' + e.message;
  }
}

function showTab(name, push = true) {
  const run = name !== 'runs';
  $('tab-run').hidden = !run;
  $('tab-runs').hidden = run;
  $('tabbtn-run').className = 'tab' + (run ? ' active' : '');
  $('tabbtn-runs').className = 'tab' + (run ? '' : ' active');
  // Keep the URL in sync so a refresh stays on the current tab.
  const path = run ? '/' : '/runs';
  if (push && location.pathname !== path) history.pushState({}, '', path);
  if (!run) loadRuns();  // always show fresh history when opening the tab
}

// Pick the tab from the URL on load, and follow back/forward.
window.addEventListener('popstate', () => showTab(location.pathname === '/runs' ? 'runs' : 'run', false));
showTab(location.pathname === '/runs' ? 'runs' : 'run', false);
loadRuns();  // prime history so the Runs tab is ready even when landing on /
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        # Both tab URLs serve the same page; the client picks the tab from the
        # path so a refresh on /runs stays on the Runs tab.
        if self.path in ("/", "/index.html", "/runs"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif self.path == "/api/runs":
            rows, err = try_recent_runs(20)
            self._send(200, json.dumps({"runs": rows or [], "error": err}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def _params(self, req):
        return dict(
            system_prompt=req.get("system_prompt", ""),
            question=req.get("question", ""),
            temperature=float(req.get("temperature", 1.0)),
            thinking_budget=int(req.get("thinking_budget", -1)),
            enable_web=bool(req.get("enable_web", False)),
            logprobs=5 if req.get("logprobs") else None,
        )

    def do_POST(self):
        try:
            if self.path == "/ask":
                self._handle_ask(self._read_json())
            elif self.path == "/sweep":
                self._handle_sweep(self._read_json())
            else:
                self._send(404, json.dumps({"error": "not found"}))
        except Exception as e:
            self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}))

    def _handle_ask(self, req):
        n = max(1, min(MAX_SAMPLES, int(req.get("n", 1))))
        p = max(1, min(MAX_PARALLELISM, int(req.get("p", llm.parallelism()))))
        params = self._params(req)

        records, wall_ms, capped = run_async(run_batch(params, n, p, MAX_RUN_COST))
        agg = aggregate(records, wall_ms, p, capped)
        agg["cost_cap"] = MAX_RUN_COST

        # Persist the whole run (config + aggregate + every request).
        doc = {
            "created_at": datetime.now(timezone.utc),
            "type": "run",
            "config": {
                "model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                "n": n, "p": p, **params, "cost_cap": MAX_RUN_COST,
            },
            "aggregate": {k: v for k, v in agg.items() if k != "distinct"},
            "distinct": agg["distinct"],
            "requests": records,
        }
        run_id, persist_error = try_save_run(doc)
        agg["run_id"] = run_id
        agg["persist_error"] = persist_error
        self._send(200, json.dumps(agg))

    def _handle_sweep(self, req):
        n = max(1, min(MAX_SAMPLES, int(req.get("n", 1))))
        # Parse "1, 5, 10, 25, 50" -> [1,5,10,25,50], clamped and de-duped.
        raw = str(req.get("p_list", "1,5,10,25,50")).replace(" ", "")
        p_list, seen = [], set()
        for tok in raw.split(","):
            if not tok:
                continue
            p = max(1, min(MAX_PARALLELISM, int(tok)))
            if p not in seen:
                seen.add(p)
                p_list.append(p)
        p_list = p_list or [llm.parallelism()]
        params = self._params(req)

        steps, spent = run_async(run_sweep(params, n, p_list, MAX_RUN_COST))
        best_rps = max((s.get("throughput_rps", 0) for s in steps), default=0)
        result = {
            "steps": steps, "n": n, "p_list": p_list,
            "total_cost": spent, "cost_cap": MAX_RUN_COST,
        }

        doc = {
            "created_at": datetime.now(timezone.utc),
            "type": "sweep",
            "config": {
                "model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                "n": n, "p_list": p_list, **params, "cost_cap": MAX_RUN_COST,
            },
            "summary": {"total_cost": spent, "best_rps": best_rps},
            "steps": steps,
        }
        run_id, persist_error = try_save_run(doc)
        result["run_id"] = run_id
        result["persist_error"] = persist_error
        self._send(200, json.dumps(result))

    def log_message(self, *args):
        pass  # quiet


def _watch_and_restart(poll=1.0):
    """Dev auto-reload with no extra deps: poll the mtimes of our source files
    and re-exec the whole process when one changes. ThreadingHTTPServer sets
    allow_reuse_address, so the port is free immediately on restart. In-flight
    requests are dropped — fine for a hand-driven probe. Disable with
    GEMINI_WATCH=0."""
    src = lambda: [__file__] + glob.glob(os.path.join(os.path.dirname(__file__) or ".", "llm", "*.py"))
    seen = {f: os.path.getmtime(f) for f in src() if os.path.exists(f)}
    while True:
        time.sleep(poll)
        for f in src():
            try:
                m = os.path.getmtime(f)
            except OSError:
                continue
            if seen.get(f) != m:
                print(f"\n{os.path.relpath(f)} changed — restarting", flush=True)
                os.execv(sys.executable, [sys.executable] + sys.argv)


if __name__ == "__main__":
    watch = os.getenv("GEMINI_WATCH", "1") != "0"
    print(f"Gemini load-test console on http://localhost:{PORT}"
          f"{' (watching)' if watch else ''}")
    if watch:
        threading.Thread(target=_watch_and_restart, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
