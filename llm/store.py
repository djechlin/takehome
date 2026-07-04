"""Persist load-test runs to a local MongoDB.

One document per run in db `evertune_loadtest`, collection `run`. The document
holds the config, the rolled-up aggregate, and the full per-request array — so a
run is self-contained and you can diff P=10 vs P=50 later by querying the
collection. Best-effort: if Mongo is down the caller still gets its results,
just with a persist error attached.

    MONGO_URI  (default: mongodb://localhost:27017)
    MONGO_DB   (default: evertune_loadtest)
"""
import os

from pymongo import MongoClient
from pymongo.errors import PyMongoError

_client = None


def _collection():
    global _client
    if _client is None:
        _client = MongoClient(
            os.getenv("MONGO_URI", "mongodb://localhost:27017"),
            serverSelectionTimeoutMS=1500,
        )
    return _client[os.getenv("MONGO_DB", "evertune_loadtest")]["run"]


def save_run(doc):
    """Insert one run document. Returns its str _id, or raises PyMongoError."""
    result = _collection().insert_one(doc)
    return str(result.inserted_id)


def try_save_run(doc):
    """Best-effort save. Returns (run_id, error) — exactly one is None."""
    try:
        return save_run(doc), None
    except PyMongoError as e:
        return None, f"{type(e).__name__}: {e}"


def recent_runs(limit=20):
    """Return compact summaries of the most recent runs/sweeps, newest first,
    JSON-serializable (ObjectId and datetime stringified)."""
    cur = (
        _collection()
        .find({}, {"created_at": 1, "type": 1, "config": 1, "aggregate": 1, "summary": 1})
        .sort("created_at", -1)
        .limit(limit)
    )
    out = []
    for d in cur:
        cfg = d.get("config") or {}
        agg = d.get("aggregate") or {}
        summ = d.get("summary") or {}
        lat = agg.get("latency_ms") or {}
        is_sweep = d.get("type") == "sweep"
        out.append({
            "id": str(d["_id"]),
            "created_at": d["created_at"].isoformat() if d.get("created_at") else None,
            "type": d.get("type", "run"),
            "model": cfg.get("model"),
            "question": (cfg.get("question") or "")[:70],
            "n": cfg.get("n"),
            "p": cfg.get("p_list") if is_sweep else cfg.get("p"),
            "temperature": cfg.get("temperature"),
            "web": bool(cfg.get("enable_web")),
            "ok": agg.get("ok"),
            "errors": agg.get("error_count"),
            "throughput_rps": agg.get("throughput_rps") or summ.get("best_rps"),
            "p50": lat.get("p50"),
            "p95": lat.get("p95"),
            "p100": lat.get("p100"),
            "wall_ms": agg.get("wall_ms"),
            "cost": (agg.get("cost") or {}).get("total") or summ.get("total_cost"),
        })
    return out


def try_recent_runs(limit=20):
    """Best-effort read. Returns (rows, error) — exactly one is None."""
    try:
        return recent_runs(limit), None
    except PyMongoError as e:
        return None, f"{type(e).__name__}: {e}"
