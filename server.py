"""Tiny web app to try the Gemini provider by hand — one question at a time,
no chat context. Serves a form and calls Gemini.ask_generic_question.

Run:  python3 server.py     ->  http://localhost:4454
"""
import asyncio
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from llm import Gemini

PORT = 4454
llm = Gemini()

# Approximate Gemini 2.5 Flash list price (USD per 1M tokens). Thinking tokens
# bill at the output rate and are already folded into output_tokens. Override
# via env if prices change or a different model is used.
PRICE_IN_PER_M = float(os.getenv("GEMINI_PRICE_INPUT_PER_M", "0.30"))
PRICE_OUT_PER_M = float(os.getenv("GEMINI_PRICE_OUTPUT_PER_M", "2.50"))

# Run ONE event loop for the whole process, in a background thread. The
# google-genai async client's httpx pool binds to the loop it's first used on;
# using asyncio.run() per request would create and close a new loop each time,
# so the second request would hit "RuntimeError: Event loop is closed". Each
# request thread dispatches its coroutine onto this shared loop and blocks.
_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True).start()


def run_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, _loop).result()

PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Gemini 2.5 Flash — one-shot</title>
<style>
  body { font: 15px/1.5 -apple-system, system-ui, sans-serif; max-width: 720px;
         margin: 40px auto; padding: 0 16px; color: #1a1a1a; }
  h1 { font-size: 18px; }
  label { display: block; margin: 14px 0 4px; font-weight: 600; font-size: 13px; }
  textarea, input { width: 100%; box-sizing: border-box; padding: 8px;
                    font: inherit; border: 1px solid #ccc; border-radius: 6px; }
  textarea { resize: vertical; }
  button { margin-top: 16px; padding: 9px 18px; font: inherit; font-weight: 600;
           border: 0; border-radius: 6px; background: #1a1a1a; color: #fff; cursor: pointer; }
  button:disabled { opacity: .5; cursor: default; }
  /* Analytics panel — the interesting part, so it sits ABOVE the answer. */
  #panel { display: none; margin-top: 22px; border: 1px solid #e3e3e3;
           border-radius: 10px; padding: 14px 16px; background: #fafafa; }
  #panel.show { display: block; }
  .tiles { display: flex; gap: 28px; flex-wrap: wrap; }
  .tile .n { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }
  .tile .k { font-size: 11px; color: #777; text-transform: uppercase; letter-spacing: .04em; }
  .detail { margin-top: 12px; padding-top: 12px; border-top: 1px solid #ececec;
            font-size: 12px; color: #555; display: flex; gap: 18px; flex-wrap: wrap; }
  .detail b { color: #1a1a1a; font-variant-numeric: tabular-nums; font-weight: 600; }
  .hint { color: #aaa; font-size: 11px; margin-top: 8px; }
  #answer { white-space: pre-wrap; margin-top: 16px; padding: 14px; background: #f5f5f5;
            border-radius: 8px; min-height: 20px; }
  #answer .lbl { font-size: 11px; color: #999; text-transform: uppercase;
                 letter-spacing: .04em; display: block; margin-bottom: 6px; }
  .err { color: #b00020; }
</style>
</head>
<body>
  <h1>Gemini 2.5 Flash (Vertex) — single question</h1>
  <label>System prompt</label>
  <input id="sys" value="You are a helpful assistant.">
  <label>Question</label>
  <textarea id="q" rows="4">What is the capital of France?</textarea>
  <label>Temperature</label>
  <input id="temp" type="number" step="0.1" min="0" max="2" value="0.7" style="width:100px">
  <br><button id="go" onclick="ask()">Ask</button>

  <div id="panel">
    <div class="tiles">
      <div class="tile"><div class="n" id="t-latency">–</div><div class="k">latency</div></div>
      <div class="tile"><div class="n" id="t-cost">–</div><div class="k">cost</div></div>
      <div class="tile"><div class="n" id="t-total">–</div><div class="k">total tokens</div></div>
    </div>
    <div class="detail">
      <span>prompt <b id="d-prompt">–</b></span>
      <span>thinking <b id="d-think">–</b></span>
      <span>answer <b id="d-answer">–</b></span>
      <span>in <b id="d-incost">–</b> · out <b id="d-outcost">–</b></span>
      <span>≈ <b id="d-per1k">–</b> / 1k requests</span>
    </div>
    <div class="hint" id="d-price"></div>
  </div>

  <div id="answer"></div>
<script>
const $ = id => document.getElementById(id);
const usd = (n, p = 2) => '$' + (n < 0.01 ? n.toPrecision(p) : n.toFixed(p));

async function ask() {
  $('go').disabled = true;
  $('panel').className = '';
  $('answer').className = '';
  $('answer').textContent = 'thinking…';
  try {
    const r = await fetch('/ask', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        system_prompt: $('sys').value,
        question: $('q').value,
        temperature: parseFloat($('temp').value),
      })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'request failed');
    const u = d.usage, c = d.cost;
    $('t-latency').textContent = d.latency_ms + ' ms';
    $('t-cost').textContent = usd(c.total, 2);
    $('t-total').textContent = u.total.toLocaleString();
    $('d-prompt').textContent = u.input.toLocaleString();
    $('d-think').textContent = u.reasoning.toLocaleString();
    $('d-answer').textContent = u.answer.toLocaleString();
    $('d-incost').textContent = usd(c.input, 2);
    $('d-outcost').textContent = usd(c.output, 2);
    $('d-per1k').textContent = usd(c.per_1k, 2);
    $('d-price').textContent =
      `list price $${d.price.input_per_m}/1M in · $${d.price.output_per_m}/1M out (approx)`;
    $('panel').className = 'show';
    $('answer').innerHTML = '<span class="lbl">response</span>';
    $('answer').append(document.createTextNode(d.answer));
  } catch (e) {
    $('answer').className = 'err';
    $('answer').textContent = e.message;
  } finally {
    $('go').disabled = false;
  }
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
            start = time.time()
            resp = run_async(llm.ask_generic_question(
                system_prompt=req.get("system_prompt", ""),
                question=req.get("question", ""),
                temperature=float(req.get("temperature", 0.7)),
            ))
            latency_ms = int((time.time() - start) * 1000)
            in_tok, out_tok = resp.input_tokens, resp.output_tokens
            reasoning = resp.reasoning_tokens
            cost_in = in_tok / 1e6 * PRICE_IN_PER_M
            cost_out = out_tok / 1e6 * PRICE_OUT_PER_M
            cost_total = cost_in + cost_out
            self._send(200, json.dumps({
                "answer": resp.answer,
                "latency_ms": latency_ms,
                "usage": {
                    "input": in_tok,
                    "reasoning": reasoning,
                    "answer": out_tok - reasoning,
                    "output": out_tok,
                    "total": in_tok + out_tok,
                },
                "cost": {
                    "input": cost_in,
                    "output": cost_out,
                    "total": cost_total,
                    "per_1k": cost_total * 1000,
                },
                "price": {"input_per_m": PRICE_IN_PER_M, "output_per_m": PRICE_OUT_PER_M},
            }))
        except Exception as e:
            self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}))

    def log_message(self, *args):
        pass  # quiet


if __name__ == "__main__":
    print(f"Gemini one-shot web app on http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
