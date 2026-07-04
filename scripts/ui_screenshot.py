"""Drive the load-test console in a real (headless) browser and screenshot it,
so we can eyeball the UI as a user without hitting Vertex or spending money.

Covers all three views: the form, a populated single-run panel, and a populated
P-sweep table — by calling the page's own render()/renderSweep() with MOCK
payloads (the exact code a real run uses). It also seeds two fake docs into
MongoDB so the "Recent runs" table renders end-to-end via the real /runs
endpoint, then removes them.

Assumes the server is already up on localhost:4454 (make serve).

Run:  .venv/bin/python scripts/ui_screenshot.py [out_dir]
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright

from llm.store import _collection

URL = "http://localhost:4454"
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
MARK = "_ui_screenshot_seed"

# A fake /ask response, shaped exactly like server.aggregate() output.
MOCK = {
    "parallelism": 25,
    "requested": 100,
    "ok": 98,
    "error_count": 2,
    "skipped": 0,
    "errors": ["ResourceExhausted: 429 Quota exceeded", "DeadlineExceeded: 504"],
    "capped": False,
    "cost_cap": 10.0,
    "wall_ms": 8400,
    "throughput_rps": 11.67,
    "grounded": False,
    "avg_logprob": -0.212,
    "distinct": [
        {"answer": "Nike, Brooks, Hoka, Asics, New Balance", "count": 61},
        {"answer": "Brooks, Nike, Hoka, Saucony, Asics", "count": 22},
        {"answer": "Hoka, Nike, Brooks, New Balance, Adidas", "count": 11},
        {"answer": "Nike, Adidas, Brooks, Asics, Mizuno", "count": 4},
    ],
    "sources": [],
    "search_queries": [],
    "latency_ms": {"p50": 1900, "p95": 3400, "p99": 5200, "p100": 6100},
    "queue_ms": {"p50": 40, "p100": 2600},
    "usage": {
        "input": 900,
        "reasoning": 4200,
        "answer": 5100,
        "output": 9300,
        "total": 10200,
    },
    "cost": {"total": 0.0241, "per_request": 0.000246, "per_1k": 0.246},
    "price": {"input_per_m": 0.30, "output_per_m": 2.50},
    "run_id": "665f1c2a9b3e4d0011aa22bb",
    "persist_error": None,
}


def _step(p, rps, p50, p95, p100, qmax, err):
    return {
        "parallelism": p,
        "ok": 20 - err,
        "error_count": err,
        "skipped": 0,
        "throughput_rps": rps,
        "queue_ms": {"p50": 0, "p100": qmax},
        "latency_ms": {"p50": p50, "p95": p95, "p99": p100, "p100": p100},
        "usage": {"reasoning": 1800},
        "cost": {"total": 0.006},
    }


# A fake /sweep response — a curve that clearly degrades past P=25.
MOCK_SWEEP = {
    "n": 20,
    "p_list": [1, 5, 10, 25, 50],
    "total_cost": 0.031,
    "cost_cap": 10.0,
    "run_id": "665f1c2a9b3e4d0011aa9999",
    "persist_error": None,
    "steps": [
        _step(1, 1.1, 900, 1100, 1300, 0, 0),
        _step(5, 5.0, 950, 1400, 1700, 20, 0),
        _step(10, 9.2, 1050, 1900, 2400, 120, 0),
        _step(25, 11.6, 1900, 3400, 6100, 2600, 0),  # best throughput
        _step(50, 10.4, 4200, 9800, 16000, 12000, 6),  # degraded: errors + tail blowup
    ],
}


def seed_runs():
    now = datetime.now(timezone.utc)
    _collection().insert_many(
        [
            {
                "created_at": now,
                "type": "run",
                MARK: True,
                "config": {
                    "n": 100,
                    "p": 25,
                    "question": "What are the best running shoe brands?",
                },
                "aggregate": {
                    "throughput_rps": 11.67,
                    "ok": 98,
                    "error_count": 2,
                    "distinct_count": 83,
                    "latency_ms": {"p95": 3400},
                    "cost": {"total": 0.0241},
                },
            },
            {
                "created_at": now,
                "type": "sweep",
                MARK: True,
                "config": {
                    "n": 20,
                    "p_list": [1, 5, 10, 25, 50],
                    "question": "What are the best running shoe brands?",
                },
                "summary": {"total_cost": 0.031, "best_rps": 11.6},
            },
        ]
    )


def main():
    seed_runs()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 900, "height": 1500})
            page.goto(URL, wait_until="networkidle")

            page.screenshot(path=f"{OUT}/ui_form.png", full_page=True)
            print(f"wrote {OUT}/ui_form.png")

            page.evaluate("(d) => render(d)", MOCK)
            page.wait_for_selector("#panel.show")
            page.screenshot(path=f"{OUT}/ui_results.png", full_page=True)
            print(f"wrote {OUT}/ui_results.png")

            page.evaluate("(d) => renderSweep(d)", MOCK_SWEEP)
            page.wait_for_selector("#sweeppanel.show")
            page.screenshot(path=f"{OUT}/ui_sweep.png", full_page=True)
            print(f"wrote {OUT}/ui_sweep.png")

            # Second tab via its own URL — proves a refresh on /runs stays put.
            page.goto(URL + "/runs", wait_until="networkidle")
            page.wait_for_selector("#tab-runs:not([hidden])")
            page.wait_for_selector("#runstable tr")
            assert page.url.endswith("/runs"), page.url
            page.screenshot(path=f"{OUT}/ui_runs_tab.png", full_page=True)
            print(f"wrote {OUT}/ui_runs_tab.png (url={page.url})")

            browser.close()
    finally:
        _collection().delete_many({MARK: True})


if __name__ == "__main__":
    main()
