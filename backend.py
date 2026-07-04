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
import hashlib
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


# Count in-flight /ask and /sweep requests so the file-watcher can hold a
# restart until they finish — otherwise auto-reload drops a long run mid-flight.
_inflight = 0
_inflight_lock = threading.Lock()


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
        # Total tokens (in+out) over wall time — the DSQ-relevant throughput.
        "tokens_per_min": (
            round((in_tok + out_tok) / (wall_ms / 60000)) if wall_ms else 0
        ),
        "grounded": any(r["grounded"] for r in ok),
        "distinct": distinct,
        "distinct_count": len(distinct),  # unique answers (grouped by hash)
        "sources": sources,
        "search_queries": queries,
        "avg_logprob": (sum(logprobs) / len(logprobs)) if logprobs else None,
        "latency_ms": {
            "p50": _pct(service, 50),
            "p95": _pct(service, 95),
            "p99": _pct(service, 99),
            "p100": service[-1] if service else 0,
        },
        "queue_ms": {"p50": _pct(waits, 50), "p100": waits[-1] if waits else 0},
        "usage": {
            "input": in_tok,
            "reasoning": reasoning,
            "answer": out_tok - reasoning,
            "output": out_tok,
            "total": in_tok + out_tok,
        },
        "cost": {
            "total": cost_total,
            "per_request": cost_total / n_ok,
            "per_1k": cost_total / n_ok * 1000,
        },
        "price": {"input_per_m": PRICE_IN_PER_M, "output_per_m": PRICE_OUT_PER_M},
    }


ANSWER_PREVIEW_CHARS = 100


def _compact_answer(text):
    """Full answers × N blow past Mongo's 16MB doc limit, so we persist a short
    preview + a hash (to still tell responses apart and group duplicates) + the
    original length, instead of the whole text."""
    text = text or ""
    return {
        "answer_preview": text[:ANSWER_PREVIEW_CHARS],
        "answer_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
        "answer_len": len(text),
    }


def compact_for_storage(records, distinct):
    """Build storable copies of the per-request records and the distinct-answer
    list with full answers replaced by preview+hash. The live HTTP response
    keeps the full text — only what we save to Mongo is shrunk."""
    slim = []
    for r in records:
        if "answer" in r:
            c = {k: v for k, v in r.items() if k != "answer"}
            c.update(_compact_answer(r["answer"]))
            slim.append(c)
        else:
            slim.append(r)
    slim_distinct = [
        {**_compact_answer(d["answer"]), "count": d["count"]} for d in distinct
    ]
    return slim, slim_distinct


class Handler(BaseHTTPRequestHandler):
    """Backend API only — runs load tests, reads run history. No HTML; the web
    server (web.py) serves the UI and proxies here."""

    def _send(self, code, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, json.dumps({"ok": True}))
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

        started = datetime.now(timezone.utc)
        records, wall_ms, capped = run_async(run_batch(params, n, p, MAX_RUN_COST))
        ended = datetime.now(timezone.utc)
        agg = aggregate(records, wall_ms, p, capped)
        agg["cost_cap"] = MAX_RUN_COST

        # Persist the run (config + aggregate + every request). Answers are
        # stored as preview+hash, not full text, to stay under Mongo's 16MB cap.
        slim_records, slim_distinct = compact_for_storage(records, agg["distinct"])
        doc = {
            "created_at": ended,
            "started_at": started,
            "ended_at": ended,
            "type": "run",
            "config": {
                "model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                "n": n,
                "p": p,
                **params,
                "cost_cap": MAX_RUN_COST,
            },
            "aggregate": {k: v for k, v in agg.items() if k != "distinct"},
            "distinct": slim_distinct,
            "requests": slim_records,
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

        started = datetime.now(timezone.utc)
        steps, spent = run_async(run_sweep(params, n, p_list, MAX_RUN_COST))
        ended = datetime.now(timezone.utc)
        best_rps = max((s.get("throughput_rps", 0) for s in steps), default=0)
        result = {
            "steps": steps,
            "n": n,
            "p_list": p_list,
            "total_cost": spent,
            "cost_cap": MAX_RUN_COST,
        }

        doc = {
            "created_at": ended,
            "started_at": started,
            "ended_at": ended,
            "type": "sweep",
            "config": {
                "model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                "n": n,
                "p_list": p_list,
                **params,
                "cost_cap": MAX_RUN_COST,
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


if __name__ == "__main__":
    # Cloud Run injects PORT; bind 0.0.0.0 so the container is reachable.
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    print(f"Gemini load-test backend API on http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
