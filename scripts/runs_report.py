"""Summarize saved load-test runs from MongoDB Atlas.

Reads through llm.store, which builds the Atlas mongodb+srv:// URI from .env
(MONGODB_URL / MONGODB_USERNAME / MONGODB_PASSWORD). Same source of truth as
the app -- no raw mongosh, no accidental localhost.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm.store import try_recent_runs


def _fmt(v):
    return v if v is not None else "-"


def _dur(ms):
    """Human-readable duration from milliseconds: 812ms, 5s, 1m12s."""
    if ms is None:
        return "-"
    if ms < 1000:
        return f"{round(ms)}ms"
    secs = ms / 1000
    if secs < 60:
        return f"{round(secs)}s"
    m, s = divmod(round(secs), 60)
    return f"{m}m{s:02d}s"


def _persec(secs):
    """Seconds-per-request: 240ms or 3.4s."""
    if secs is None:
        return "-"
    return f"{round(secs * 1000)}ms" if secs < 1 else f"{secs:.1f}s"


def main():
    rows, err = try_recent_runs(20)
    if err:
        print(f"error talking to Atlas: {err}", file=sys.stderr)
        return 1
    if not rows:
        print("no runs found in Atlas (evertune_loadtest.run is empty)")
        return 0
    for r in rows:
        print(
            f"{_fmt(r.get('created_at'))}  "
            f"N={_fmt(r.get('n'))} P={_fmt(r.get('p'))}  "
            f"reqs={_fmt(r.get('requests'))} dur={_dur(r.get('duration_ms'))}  "
            f"ok={_fmt(r.get('ok'))} err={_fmt(r.get('errors'))}  "
            f"{_fmt(r.get('throughput_rps'))}rps {_persec(r.get('sec_per_req'))}/req  "
            f"p50={_dur(r.get('p50'))}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
