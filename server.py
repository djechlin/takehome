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

from llm import Gemini, try_save_run

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
  #panel { display: none; margin-top: 22px; border: 1px solid #e3e3e3;
           border-radius: 10px; padding: 14px 16px; background: #fafafa; }
  #panel.show { display: block; }
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

  <button id="go" onclick="ask()">Run load test</button>

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

<script>
const $ = id => document.getElementById(id);
const usd = (n, p = 2) => '$' + (n < 0.01 ? n.toPrecision(p) : n.toFixed(p));
const esc = s => { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; };
const secs = ms => (ms / 1000).toFixed(1) + ' s';

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
  $('t-lat').textContent = l.p50 + '/' + l.p95 + '/' + l.p100 + ' ms';
  $('t-cost').textContent = usd(c.total, 2);
  $('t-uniq').textContent = d.distinct.length + ' / ' + d.ok;
  $('d-p').textContent = d.parallelism;
  $('d-ok').textContent = d.ok + ' / ' + d.requested;
  $('d-err').textContent = d.error_count;
  $('d-skip').textContent = d.skipped;
  $('d-p99').textContent = l.p99 + ' ms';
  $('d-queue').textContent = d.queue_ms.p50 + ' / ' + d.queue_ms.p100 + ' ms';
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
    ? ('saved to MongoDB evertune_loadtest.run · _id ' + d.run_id + '  (compare runs with `make runs`)')
    : ('not saved to MongoDB: ' + (d.persist_error || '?'));
  $('panel').className = 'show';
}
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
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path != "/ask":
            self._send(404, json.dumps({"error": "not found"}))
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")

            n = max(1, min(MAX_SAMPLES, int(req.get("n", 1))))
            p = max(1, min(MAX_PARALLELISM, int(req.get("p", llm.parallelism()))))
            params = dict(
                system_prompt=req.get("system_prompt", ""),
                question=req.get("question", ""),
                temperature=float(req.get("temperature", 1.0)),
                thinking_budget=int(req.get("thinking_budget", -1)),
                enable_web=bool(req.get("enable_web", False)),
                logprobs=5 if req.get("logprobs") else None,
            )

            records, wall_ms, capped = run_async(run_batch(params, n, p, MAX_RUN_COST))
            agg = aggregate(records, wall_ms, p, capped)
            agg["cost_cap"] = MAX_RUN_COST

            # Persist the whole run (config + aggregate + every request).
            doc = {
                "created_at": datetime.now(timezone.utc),
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
        except Exception as e:
            self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}))

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
