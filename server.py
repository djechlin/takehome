"""Tiny web app to try the Gemini provider by hand — one question at a time,
no chat context. Serves a form and calls Gemini.ask_generic_question.

Run:  python3 server.py     ->  http://localhost:4454
"""
import asyncio
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from llm import Gemini

PORT = 4454
llm = Gemini()

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
  #out { white-space: pre-wrap; margin-top: 22px; padding: 14px; background: #f5f5f5;
         border-radius: 8px; min-height: 20px; }
  .meta { color: #666; font-size: 12px; margin-top: 8px; }
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
  <div id="out"></div>
  <div class="meta" id="meta"></div>
<script>
async function ask() {
  const btn = document.getElementById('go');
  const out = document.getElementById('out');
  const meta = document.getElementById('meta');
  btn.disabled = true; out.className = ''; out.textContent = 'thinking…'; meta.textContent = '';
  try {
    const r = await fetch('/ask', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        system_prompt: document.getElementById('sys').value,
        question: document.getElementById('q').value,
        temperature: parseFloat(document.getElementById('temp').value),
      })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'request failed');
    out.textContent = d.answer;
    meta.textContent = `${d.input_tokens} in / ${d.output_tokens} out · ${d.latency_ms} ms`;
  } catch (e) {
    out.className = 'err'; out.textContent = e.message;
  } finally {
    btn.disabled = false;
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
            resp = asyncio.run(llm.ask_generic_question(
                system_prompt=req.get("system_prompt", ""),
                question=req.get("question", ""),
                temperature=float(req.get("temperature", 0.7)),
            ))
            self._send(200, json.dumps({
                "answer": resp.answer,
                "input_tokens": resp.input_tokens,
                "output_tokens": resp.output_tokens,
                "latency_ms": int((time.time() - start) * 1000),
            }))
        except Exception as e:
            self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}))

    def log_message(self, *args):
        pass  # quiet


if __name__ == "__main__":
    print(f"Gemini one-shot web app on http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
