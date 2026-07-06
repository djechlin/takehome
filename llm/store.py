"""Persist load-test runs to MongoDB.

One document per run in db `evertune_loadtest`, collection `run`. The document
holds the config, the rolled-up aggregate, and the full per-request array — so a
run is self-contained and you can diff P=10 vs P=50 later by querying the
collection. Best-effort: if Mongo is down the caller still gets its results,
just with a persist error attached.

Connection is resolved in this order:
    MONGO_URI                            explicit full URI wins (CI/overrides)
    MONGODB_URL/_USERNAME/_PASSWORD      Atlas SRV parts (from .env.local in dev,
                                         from Secret Manager -> env in prod)
    mongodb://localhost:27017            local fallback

    MONGO_DB   database name (default: evertune_loadtest)
"""

import os
from pathlib import Path
from urllib.parse import quote_plus

from pymongo import MongoClient
from pymongo.errors import PyMongoError

_client = None


def _load_env_local():
    """Load KEY=VALUE lines from .env.local at the repo root into os.environ,
    without overriding vars already set (real env / Secret Manager win)."""
    path = Path(__file__).resolve().parent.parent / ".env.local"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = val


def _mongo_uri():
    uri = os.getenv("MONGO_URI")
    if uri:
        return uri
    host = os.getenv("MONGODB_URL")
    user = os.getenv("MONGODB_USERNAME")
    pw = os.getenv("MONGODB_PASSWORD")
    if host and user and pw:
        base = host.split("://", 1)[-1].strip("/")
        return f"mongodb+srv://{quote_plus(user)}:{quote_plus(pw)}@{base}/"
    return "mongodb://localhost:27017"


_load_env_local()


def _collection():
    global _client
    if _client is None:
        _client = MongoClient(_mongo_uri(), serverSelectionTimeoutMS=1500)
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
        .find(
            {},
            {
                "created_at": 1,
                "started_at": 1,
                "ended_at": 1,
                "type": 1,
                "config": 1,
                "aggregate": 1,
                "summary": 1,
            },
        )
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
        usage = agg.get("usage") or {}
        out.append(
            {
                "id": str(d["_id"]),
                "created_at": (
                    d["created_at"].isoformat() if d.get("created_at") else None
                ),
                "started_at": (
                    d["started_at"].isoformat() if d.get("started_at") else None
                ),
                "ended_at": (d["ended_at"].isoformat() if d.get("ended_at") else None),
                "tokens_per_min": agg.get("tokens_per_min"),
                "type": d.get("type", "run"),
                "model": cfg.get("model"),
                "question": (cfg.get("question") or "")[:70],
                "n": cfg.get("n"),
                "p": cfg.get("p_list") if is_sweep else cfg.get("p"),
                "temperature": cfg.get("temperature"),
                "web": bool(cfg.get("enable_web")),
                "ok": agg.get("ok"),
                "errors": agg.get("error_count"),
                "distinct_count": agg.get("distinct_count"),
                "throughput_rps": agg.get("throughput_rps") or summ.get("best_rps"),
                "p50": lat.get("p50"),
                "in_tok": usage.get("input"),
                "out_tok": usage.get("output"),
                "cost": (agg.get("cost") or {}).get("total") or summ.get("total_cost"),
            }
        )
    return out


def try_recent_runs(limit=20):
    """Best-effort read. Returns (rows, error) — exactly one is None."""
    try:
        return recent_runs(limit), None
    except PyMongoError as e:
        return None, f"{type(e).__name__}: {e}"
