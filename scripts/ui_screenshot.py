"""Drive the load-test console in a real (headless) browser and screenshot it,
so we can eyeball the UI as a user without hitting Vertex or spending money.

It loads the page, screenshots the empty form, then calls the page's own
render() with a MOCK result payload and screenshots the populated panel — that
exercises the exact display code a real run uses, for free.

Assumes the server is already up on localhost:4454 (make serve).

Run:  .venv/bin/python scripts/ui_screenshot.py [out_dir]
"""
import sys

from playwright.sync_api import sync_playwright

URL = "http://localhost:4454"
OUT = sys.argv[1] if len(sys.argv) > 1 else "."

# A fake /ask response, shaped exactly like server.aggregate() output, so the
# page's render() paints a realistic populated panel.
MOCK = {
    "parallelism": 25, "requested": 100, "ok": 98, "error_count": 2, "skipped": 0,
    "errors": ["ResourceExhausted: 429 Quota exceeded", "DeadlineExceeded: 504"],
    "capped": False, "cost_cap": 10.0, "wall_ms": 8400, "throughput_rps": 11.67,
    "grounded": False, "avg_logprob": -0.212,
    "distinct": [
        {"answer": "Nike, Brooks, Hoka, Asics, New Balance", "count": 61},
        {"answer": "Brooks, Nike, Hoka, Saucony, Asics", "count": 22},
        {"answer": "Hoka, Nike, Brooks, New Balance, Adidas", "count": 11},
        {"answer": "Nike, Adidas, Brooks, Asics, Mizuno", "count": 4},
    ],
    "sources": [], "search_queries": [],
    "latency_ms": {"p50": 1900, "p95": 3400, "p99": 5200, "p100": 6100},
    "queue_ms": {"p50": 40, "p100": 2600},
    "usage": {"input": 900, "reasoning": 4200, "answer": 5100, "output": 9300, "total": 10200},
    "cost": {"total": 0.0241, "per_request": 0.000246, "per_1k": 0.246},
    "price": {"input_per_m": 0.30, "output_per_m": 2.50},
    "run_id": "665f1c2a9b3e4d0011aa22bb", "persist_error": None,
}


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 900, "height": 1300})
        page.goto(URL, wait_until="networkidle")

        page.screenshot(path=f"{OUT}/ui_form.png", full_page=True)
        print(f"wrote {OUT}/ui_form.png")

        page.evaluate("(d) => render(d)", MOCK)
        page.wait_for_selector("#panel.show")
        page.screenshot(path=f"{OUT}/ui_results.png", full_page=True)
        print(f"wrote {OUT}/ui_results.png")

        browser.close()


if __name__ == "__main__":
    main()
