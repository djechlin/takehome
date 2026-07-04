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
