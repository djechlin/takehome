import os
from pathlib import Path
from urllib.parse import quote_plus

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import MongoClient
from pymongo.errors import PyMongoError

_client = None


def _load_dotenv():
    """Load KEY=VALUE lines from .env at the repo root into os.environ, without
    overriding vars already set. .env is git-ignored but NOT gcloud-ignored, so
    the same file serves local dev and the deployed Cloud Run image."""
    path = Path(__file__).resolve().parent.parent / ".env"
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


_load_dotenv()


def _collection():
    global _client
    if _client is None:
        _client = MongoClient(_mongo_uri(), serverSelectionTimeoutMS=1500)
    return _client[os.getenv("MONGO_DB", "evertune_loadtest")]["run"]


def save_run(doc):
    result = _collection().insert_one(doc)
    return str(result.inserted_id)


def try_save_run(doc):
    try:
        return save_run(doc), None
    except PyMongoError as e:
        return None, f"{type(e).__name__}: {e}"


def recent_runs(limit=20):
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

        # Total experiment duration = wall clock from first request to last,
        # measured off the stored timestamps (falls back to the batch wall_ms).
        # For a sweep this spans every P step, not just one batch.
        started, ended = d.get("started_at"), d.get("ended_at")
        if started and ended:
            duration_ms = (ended - started).total_seconds() * 1000
        else:
            duration_ms = agg.get("wall_ms")

        # Total requests fired across the whole experiment. A sweep runs N at
        # each P, so it's N × (number of P steps); a single run is just N.
        n = cfg.get("n")
        if is_sweep:
            steps = len(cfg.get("p_list") or [])
            requests = n * steps if (n is not None and steps) else None
        else:
            requests = agg.get("requested") or n

        # Throughput as experiment duration / request (seconds per request,
        # amortized over the whole experiment — the inverse of req/s).
        sec_per_req = (
            (duration_ms / 1000) / requests
            if duration_ms is not None and requests
            else None
        )

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
                "duration_ms": duration_ms,
                "requests": requests,
                "sec_per_req": sec_per_req,
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
    try:
        return recent_runs(limit), None
    except PyMongoError as e:
        return None, f"{type(e).__name__}: {e}"


def run_detail(run_id, examples=10):
    """Fetch one run's per-request detail: a sample of the errors we collected
    and the successful answers. Answers were persisted as preview+hash (see
    backend.compact_for_storage), so previews may be truncated. Returns None if
    the id is malformed or no such run exists."""
    try:
        oid = ObjectId(run_id)
    except (InvalidId, TypeError):
        return None
    doc = _collection().find_one({"_id": oid})
    if not doc:
        return None

    reqs = doc.get("requests") or []
    errors = [r for r in reqs if not r.get("ok") and not r.get("skipped")]
    successes = [r for r in reqs if r.get("ok")]

    def err_view(r):
        return {
            "index": r.get("index"),
            "error": r.get("error"),
            "service_ms": r.get("service_ms"),
            "wait_ms": r.get("wait_ms"),
        }

    def ok_view(r):
        return {
            "index": r.get("index"),
            "answer": r.get("answer_preview"),
            "answer_len": r.get("answer_len"),
            "truncated": (r.get("answer_len") or 0)
            > len(r.get("answer_preview") or ""),
            "output": r.get("output"),
            "service_ms": r.get("service_ms"),
        }

    return {
        "id": str(doc["_id"]),
        "type": doc.get("type", "run"),
        "error_count": len(errors),
        "ok_count": len(successes),
        "errors": [err_view(r) for r in errors[:examples]],
        "successes": [ok_view(r) for r in successes[:examples]],
    }


def try_run_detail(run_id, examples=10):
    try:
        return run_detail(run_id, examples), None
    except PyMongoError as e:
        return None, f"{type(e).__name__}: {e}"
