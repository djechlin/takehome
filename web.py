"""Web/front-end server for the Gemini load-test console.

Serves the single-page UI and proxies its data calls (/ask, /sweep, /api/runs)
to the backend API (backend.py), which does the actual load runs and Mongo
persistence. Keeping them separate lets the backend deploy to Cloud Run while
this stays a thin local front end. Point it at a deployed backend with
BACKEND_URL.

    WEB_PORT     (default: 4454)
    BACKEND_URL  (default: http://127.0.0.1:4460)

Run:  python3 web.py     ->  http://localhost:4454
"""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

WEB_PORT = int(os.getenv("WEB_PORT", "4454"))
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:4460").rstrip("/")

# A deployed backend on Cloud Run is private (--no-allow-unauthenticated), so
# calls to it must carry a Google-signed identity token. web.py runs under a
# gcloud-authenticated user, so we mint that token with
# `gcloud auth print-identity-token` and attach it as a Bearer header — GCP's
# documented way to reach a private service. A local backend needs none, so we
# only do this when BACKEND_URL points somewhere other than localhost.
_BACKEND_HOST = urlparse(BACKEND_URL).hostname or ""
BACKEND_IS_REMOTE = _BACKEND_HOST not in ("127.0.0.1", "localhost", "::1")

# Identity tokens last ~1h; cache and refresh well before expiry (shelling out
# to gcloud on every proxied request would add ~1s of latency each time).
_token = {"value": None, "exp": 0.0}
_token_lock = threading.Lock()


def _identity_token():
    """Return a cached gcloud identity token, refreshing when near expiry."""
    with _token_lock:
        if _token["value"] and time.time() < _token["exp"]:
            return _token["value"]
        proc = subprocess.run(
            ["gcloud", "auth", "print-identity-token"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        tok = proc.stdout.strip()
        if proc.returncode != 0 or not tok:
            raise RuntimeError(
                "could not get a gcloud identity token for the private backend "
                f"({BACKEND_URL}); run `gcloud auth login`. "
                f"{proc.stderr.strip()}"
            )
        _token.update(value=tok, exp=time.time() + 3000)  # ~50 min
        return tok


# Count in-flight proxied requests so the file-watcher holds a restart until
# they finish — otherwise auto-reload drops a long run mid-flight.
_inflight = 0
_inflight_lock = threading.Lock()

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
  #runstable tr[data-id] { cursor: pointer; }
  #runstable tr[data-id]:hover td { background: #f2f6ff; }
  #runstable tr.open td { background: #eef2fb; }
  tr.detailrow td { text-align: left; white-space: normal; background: #f7f9fd;
                    padding: 14px 16px; }
  .detailcard h4 { font-size: 11px; color: #777; text-transform: uppercase;
                   letter-spacing: .04em; margin: 0 0 6px; }
  .detailcard h4:not(:first-child) { margin-top: 16px; }
  .exlist { margin: 0; padding-left: 20px; font-size: 13px; }
  .exlist li { padding: 3px 0; border-bottom: 1px dashed #e6e6e6; }
  .exlist li:last-child { border-bottom: 0; }
  .exlist .dim { color: #999; font-size: 11px; }
  .exlist code { background: #fdecef; color: #b00020; padding: 1px 5px;
                 border-radius: 4px; font-size: 12px; }
  /* size to content so it doesn't inherit the wide runs-table width */
  .steptable { width: auto; margin-top: 4px; }
  .steptable th, .steptable td { padding: 5px 16px; }
  .steptable th:first-child, .steptable td:first-child { padding-left: 2px; }
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
  <textarea id="q" rows="3">good co-op games for switch</textarea>

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
    const head = ['when', 'question', 'N', 'P', 'temp', 'web',
                  'ok/err', 'distinct', 'duration', 'rps', 's/req', 'p50',
                  'in tok', 'out tok', 'tok/min', 'cost']
      .map(h => '<th>' + h + '</th>').join('');
    const tok = v => (v == null ? '–'
      : v < 1000 ? v.toLocaleString()
      : Math.round(v / 1000).toLocaleString() + 'K');
    // seconds-per-request: sub-second shows ms, else 1 decimal of seconds
    const spr = v => (v == null ? '–'
      : v < 1 ? Math.round(v * 1000) + ' ms' : v.toFixed(1) + ' s');
    const body = rows.map(x => {
      const start = x.started_at || x.created_at;
      const when = start
        ? new Date(start).toLocaleString([],
            {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'})
        : '–';
      const p = Array.isArray(x.p) ? x.p.join('/') : (x.p ?? '–');
      const okerr = (x.ok ?? '–') + ' / ' + (x.errors ?? '–');
      return '<tr data-id="' + x.id + '" onclick="toggleRun(\\'' + x.id + '\\', this)"' +
        (x.errors ? ' class="haserr"' : '') + '>' +
        '<td>' + when + '</td>' +
        '<td style="max-width:200px;overflow:hidden;text-overflow:ellipsis" title="' + esc(x.question || '') + '">' + esc(x.question || '') + '</td>' +
        '<td>' + (x.n ?? '–') + '</td>' +
        '<td>' + p + '</td>' +
        '<td>' + (x.temperature ?? '–') + '</td>' +
        '<td>' + (x.web ? 'yes' : '–') + '</td>' +
        '<td>' + okerr + '</td>' +
        '<td>' + (x.distinct_count ?? '–') + '</td>' +
        '<td>' + secD(x.duration_ms) + '</td>' +
        '<td>' + (x.throughput_rps ?? '–') + '</td>' +
        '<td>' + spr(x.sec_per_req) + '</td>' +
        '<td>' + secD(x.p50) + '</td>' +
        '<td>' + tok(x.in_tok) + '</td>' +
        '<td>' + tok(x.out_tok) + '</td>' +
        '<td>' + tok(x.tokens_per_min) + '</td>' +
        '<td>' + (x.cost != null ? '$' + x.cost.toFixed(2) : '–') + '</td></tr>';
    }).join('');
    $('runstable').innerHTML = '<tr>' + head + '</tr>' + body;
  } catch (e) {
    $('runsempty').textContent = 'Could not load runs: ' + e.message;
  }
}

// Click a run row to expand an inline card with that run's per-request detail —
// the errors we collected and a sample of successful answers. Click again to
// close. Only one card is open at a time.
async function toggleRun(id, tr) {
  const open = document.getElementById('detail-' + id);
  document.querySelectorAll('tr.detailrow').forEach(e => e.remove());
  document.querySelectorAll('#runstable tr.open').forEach(e => e.classList.remove('open'));
  if (open) return;  // it was already open — leave it closed (toggle off)

  tr.classList.add('open');
  const row = document.createElement('tr');
  row.className = 'detailrow';
  row.id = 'detail-' + id;
  const td = document.createElement('td');
  td.colSpan = 16;
  td.innerHTML = '<span class="dim">loading…</span>';
  row.appendChild(td);
  tr.after(row);
  try {
    const r = await fetch('/api/run/' + id);
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || 'failed to load run');
    td.innerHTML = renderDetail(d.run);
  } catch (e) {
    td.innerHTML = '<div class="banner err">' + esc(e.message) + '</div>';
  }
}

function renderDetail(run) {
  if (!run) return '<div class="empty">Run not found.</div>';
  if (run.type === 'sweep') return renderSweepDetail(run);

  const li = inner => '<li>' + inner + '</li>';
  let h = '<div class="detailcard">';

  h += '<h4>Errors — showing ' + run.errors.length + ' of ' + run.error_count + '</h4>';
  h += run.errors.length
    ? '<ol class="exlist">' + run.errors.map(e => li(
        '<code>' + esc(e.error || 'error') + '</code> ' +
        '<span class="dim">#' + e.index + ' · ' + secs(e.service_ms) + '</span>'
      )).join('') + '</ol>'
    : '<div class="empty">No errors.</div>';

  h += '<h4>Success examples — showing ' + run.successes.length + ' of ' + run.ok_count + '</h4>';
  h += run.successes.length
    ? '<ol class="exlist">' + run.successes.map(s => li(
        esc(s.answer || '(empty)') + (s.truncated ? '…' : '') + ' ' +
        '<span class="dim">#' + s.index + ' · ' + (s.output ?? '?') + ' tok · ' +
        secs(s.service_ms) + '</span>'
      )).join('') + '</ol>'
    : '<div class="empty">No successful requests.</div>';

  return h + '</div>';
}

// A saved sweep expands into its per-P step curve (P vs latency/throughput/
// errors) — the same table the live sweep shows, rebuilt from the stored steps.
function renderSweepDetail(run) {
  const steps = run.steps || [];
  if (!steps.length) return '<div class="empty">No sweep steps saved.</div>';
  const tk = v => (v == null ? '–' : v < 1000 ? v.toLocaleString()
    : Math.round(v / 1000).toLocaleString() + 'K');
  const best = steps.reduce((m, s) => Math.max(m, s.throughput_rps || 0), 0);
  const head = ['P', 'ok/err', 'rps', 'p50', 'tok/min', 'cost']
    .map(h => '<th>' + h + '</th>').join('');
  const rows = steps.map(s => {
    if (s.step_skipped)
      return '<tr class="haserr"><td>' + s.parallelism +
             '</td><td colspan="5">skipped — cap reached</td></tr>';
    const cls = [];
    if ((s.throughput_rps || 0) === best && best > 0) cls.push('best');
    if (s.error_count) cls.push('haserr');
    return '<tr class="' + cls.join(' ') + '">' +
      '<td>' + s.parallelism + '</td>' +
      '<td>' + (s.ok ?? '–') + ' / ' + (s.error_count ?? '–') + '</td>' +
      '<td><b>' + (s.throughput_rps ?? '–') + '</b></td>' +
      '<td>' + secD(s.p50) + '</td>' +
      '<td>' + tk(s.tokens_per_min) + '</td>' +
      '<td>' + (s.cost != null ? usd(s.cost, 3) : '–') + '</td></tr>';
  }).join('');
  return '<div class="detailcard"><h4>P-sweep — ' + steps.length +
    ' steps (best-throughput row highlighted)</h4>' +
    '<table class="steptable"><tr>' + head + '</tr>' + rows + '</table></div>';
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


# Data paths are proxied verbatim to the backend; everything else serves the UI.
_PROXY_PATHS = ("/ask", "/sweep", "/api/runs")


def _is_proxy(path):
    # /api/run/<id> is dynamic, so match it by prefix as well as the fixed set.
    return path in _PROXY_PATHS or path.startswith("/api/run/")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _proxy(self, method, body=None):
        """Forward the request to the backend and relay its response. Long runs
        are held open; the backend does the work and Mongo persistence."""
        global _inflight
        with _inflight_lock:
            _inflight += 1
        try:
            headers = {"Content-Type": "application/json"}
            if BACKEND_IS_REMOTE:
                headers["Authorization"] = "Bearer " + _identity_token()
            req = urllib.request.Request(
                BACKEND_URL + self.path,
                data=body,
                method=method,
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=3600) as r:
                self._send(r.status, r.read(), r.headers.get("Content-Type"))
        except urllib.error.HTTPError as e:
            self._send(
                e.code, e.read(), e.headers.get("Content-Type", "application/json")
            )
        except Exception as e:
            self._send(
                502,
                json.dumps({"error": f"backend unreachable ({BACKEND_URL}): {e}"}),
            )
        finally:
            with _inflight_lock:
                _inflight -= 1

    def do_GET(self):
        # Both tab URLs serve the same page; the client picks the tab from the
        # path so a refresh on /runs stays on the Runs tab.
        if self.path in ("/", "/index.html", "/runs"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif _is_proxy(self.path):
            self._proxy("GET")
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if _is_proxy(self.path):
            length = int(self.headers.get("Content-Length", 0))
            self._proxy("POST", self.rfile.read(length) or b"{}")
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def log_message(self, *args):
        pass  # quiet


def _watch_and_restart(poll=1.0):
    """Dev auto-reload: re-exec when web.py changes, DEFERRED until no proxied
    request is in flight so a long run is never dropped. Disable GEMINI_WATCH=0."""
    seen = {__file__: os.path.getmtime(__file__)}
    pending = False
    while True:
        time.sleep(poll)
        try:
            m = os.path.getmtime(__file__)
        except OSError:
            continue
        if seen[__file__] != m:
            seen[__file__] = m
            if not pending:
                print("web.py changed — restart pending", flush=True)
            pending = True
        if pending:
            with _inflight_lock:
                busy = _inflight
            if not busy:
                print("restarting", flush=True)
                os.execv(sys.executable, [sys.executable] + sys.argv)


if __name__ == "__main__":
    watch = os.getenv("GEMINI_WATCH", "1") != "0"
    print(
        f"Gemini load-test UI on http://localhost:{WEB_PORT}"
        f"  (backend: {BACKEND_URL})"
        f"{' (watching)' if watch else ''}"
    )
    if watch:
        threading.Thread(target=_watch_and_restart, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", WEB_PORT), Handler).serve_forever()
