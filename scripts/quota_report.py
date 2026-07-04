"""Report the Vertex generate_content quotas that bound our load — QPM and
input/output TPM — for a given base model and region. These are the numbers
that decide "will it hold at production scale": you can hit them from a modest
in-region setup, so knowing them matters more than raw client horsepower.

Reads the Service Usage consumer-quota API with your gcloud credentials.

    GOOGLE_CLOUD_PROJECT=evertune-tests .venv/bin/python scripts/quota_report.py
    # optional: MODEL=gemini-2.5-flash REGION=us-central1
"""

import json
import os
import subprocess
import urllib.request

PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "evertune-tests")
REGION = os.getenv("REGION", "us-central1")
MODEL = os.getenv("MODEL", "gemini-2.5-flash")

METRICS = {
    "aiplatform.googleapis.com/generate_content_requests_per_minute_per_project_per_base_model": "QPM (requests/min)",
    "aiplatform.googleapis.com/generate_content_input_tokens_per_minute_per_base_model": "input TPM (tokens/min)",
    "aiplatform.googleapis.com/generate_content_output_tokens_per_minute_per_base_model": "output TPM (tokens/min)",
}

BASE = "https://serviceusage.googleapis.com/v1beta1"
URL = (
    f"{BASE}/projects/{PROJECT}/services/aiplatform.googleapis.com/consumerQuotaMetrics"
)


def token():
    return (
        subprocess.check_output(["gcloud", "auth", "print-access-token"])
        .decode()
        .strip()
    )


def fetch_metrics(tok):
    """Page through every consumer-quota metric."""
    metrics, page = [], None
    while True:
        url = f"{URL}?pageSize=200" + (f"&pageToken={page}" if page else "")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
        with urllib.request.urlopen(req) as r:
            d = json.load(r)
        metrics += d.get("metrics", [])
        page = d.get("nextPageToken")
        if not page:
            return metrics


def limit_for(metric, region, model):
    """Return (effective_limit, region, base_model) for the bucket that best
    matches our (region, model): an EXACT base_model match wins over a generic
    default bucket, and a region match beats a global one."""
    best = None
    for lim in metric.get("consumerQuotaLimits", []):
        for b in lim.get("quotaBuckets", []):
            dims = b.get("dimensions", {}) or {}
            reg, bm = dims.get("region", ""), dims.get("base_model", "")
            if reg and reg != region:
                continue
            if bm and bm != model:  # exact model, not substring (avoids -lite-tts)
                continue
            eff = b.get("effectiveLimit", b.get("defaultLimit"))
            score = (2 if bm == model else 0) + (1 if reg == region else 0)
            if best is None or score > best[0]:
                best = (score, eff, reg or "(any region)", bm or "(default bucket)")
    return best[1:] if best else None


def fmt(eff):
    # -1 = no fixed cap (dynamic shared quota / served best-effort).
    if str(eff) == "-1":
        return "-1 = no fixed cap (dynamic shared quota)"
    try:
        return f"{int(eff):,}"
    except (TypeError, ValueError):
        return str(eff)


def dump_buckets(metric, region):
    """Print every bucket for this metric in `region` (all base models), so we
    can see exactly which model-specific buckets exist. Set DUMP=1 to enable."""
    for lim in metric.get("consumerQuotaLimits", []):
        for b in lim.get("quotaBuckets", []):
            dims = b.get("dimensions", {}) or {}
            reg, bm = dims.get("region", ""), dims.get("base_model", "")
            if reg and reg != region:
                continue
            eff = b.get("effectiveLimit", b.get("defaultLimit"))
            print(f"    base_model={bm or '(default)':32} {fmt(eff)}")


def main():
    print(f"project={PROJECT}  region={REGION}  model={MODEL}\n")
    metrics = {m["metric"]: m for m in fetch_metrics(token())}
    for name, label in METRICS.items():
        m = metrics.get(name)
        got = limit_for(m, REGION, MODEL) if m else None
        if got:
            eff, reg, bm = got
            print(f"{label:26} {fmt(eff):<40} [region={reg}, base_model={bm}]")
        else:
            print(f"{label:26} not found")
        if os.getenv("DUMP") and m:
            dump_buckets(m, REGION)


if __name__ == "__main__":
    main()
