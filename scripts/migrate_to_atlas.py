"""One-shot migration of saved load-test runs from local MongoDB to Atlas.

Copies db `evertune_loadtest`, collection `run` from mongodb://localhost:27017
into the Atlas cluster resolved by llm.store (from .env.local). Idempotent:
documents are upserted by their existing _id, so re-running won't duplicate.

    python scripts/migrate_to_atlas.py          # migrate
    python scripts/migrate_to_atlas.py --dry-run # count only, no writes
"""

import sys

from pymongo import MongoClient, ReplaceOne

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from llm import store  # noqa: E402  (uses its Atlas URI + .env.local loading)

SRC_URI = "mongodb://localhost:27017"
DB = "evertune_loadtest"
COLL = "run"


def main():
    dry = "--dry-run" in sys.argv

    src = MongoClient(SRC_URI, serverSelectionTimeoutMS=3000)[DB][COLL]
    docs = list(src.find({}))
    print(f"source {SRC_URI} {DB}.{COLL}: {len(docs)} docs")

    dst = store._collection()
    dst_uri = store._mongo_uri().split("@")[-1]
    print(
        f"target atlas ...@{dst_uri} {DB}.{COLL}: {dst.count_documents({})} docs (before)"
    )

    if dry:
        print("dry-run: no writes")
        return
    if not docs:
        print("nothing to migrate")
        return

    ops = [ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in docs]
    res = dst.bulk_write(ops, ordered=False)
    print(
        f"upserted={res.upserted_count} matched={res.matched_count} "
        f"modified={res.modified_count}"
    )
    print(f"target atlas {DB}.{COLL}: {dst.count_documents({})} docs (after)")


if __name__ == "__main__":
    main()
