"""Tests for the load-test aggregation and the Mongo run store.

These don't touch Vertex — aggregate() is pure, and the store test uses fake
records and skips if MongoDB isn't running locally.

    make test   (or)   .venv/bin/python -m pytest -q tests/test_loadtest.py
"""
from datetime import datetime, timezone

import pytest

from server import aggregate
from llm import try_save_run
from llm.store import _collection


def _fake_records():
    return [
        {"ok": True, "index": 0, "answer": "Nike, Brooks", "input": 10, "output": 20,
         "reasoning": 5, "cost": 0.0001, "grounded": False, "sources": [],
         "search_queries": [], "avg_logprob": None, "wait_ms": 2, "service_ms": 800,
         "start_offset_ms": 2},
        {"ok": True, "index": 1, "answer": "Nike, Hoka", "input": 10, "output": 22,
         "reasoning": 6, "cost": 0.00011, "grounded": False, "sources": [],
         "search_queries": [], "avg_logprob": None, "wait_ms": 5, "service_ms": 950,
         "start_offset_ms": 5},
        {"ok": False, "index": 2, "error": "ResourceExhausted: 429", "wait_ms": 3,
         "service_ms": 40, "start_offset_ms": 3},
    ]


def test_aggregate_counts_and_latency():
    agg = aggregate(_fake_records(), wall_ms=1000, p=2, capped=False)

    assert agg["ok"] == 2
    assert agg["error_count"] == 1
    assert agg["requested"] == 3
    assert agg["parallelism"] == 2
    # Two distinct answers, each seen once.
    assert len(agg["distinct"]) == 2
    # Percentiles come from the two successful service times (800, 950).
    assert agg["latency_ms"]["p50"] in (800, 950)
    assert agg["latency_ms"]["p100"] == 950
    # 2 ok requests over 1s wall = 2 rps.
    assert agg["throughput_rps"] == 2.0
    # Errors don't contribute cost.
    assert agg["cost"]["total"] == pytest.approx(0.00021)


def test_store_roundtrip():
    """Save a run and read it back. Skips cleanly if Mongo isn't up."""
    doc = {
        "created_at": datetime.now(timezone.utc),
        "config": {"n": 3, "p": 2},
        "aggregate": {"ok": 2, "error_count": 1},
        "requests": _fake_records(),
        "_test_marker": True,
    }
    run_id, err = try_save_run(doc)
    if err is not None:
        pytest.skip(f"MongoDB unavailable: {err}")

    assert run_id
    coll = _collection()
    from bson import ObjectId
    stored = coll.find_one({"_id": ObjectId(run_id)})
    assert stored is not None
    assert stored["config"]["p"] == 2
    assert len(stored["requests"]) == 3
    coll.delete_one({"_id": ObjectId(run_id)})  # clean up the test doc
