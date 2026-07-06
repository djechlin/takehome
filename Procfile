# Cloud Run entrypoint for the backend API (source deploy uses the Python
# buildpack, which runs this `web` process). backend.py binds 0.0.0.0 on the
# PORT env var Cloud Run injects. The web/UI (web.py) is NOT deployed — it runs
# locally and proxies here via BACKEND_URL=<the Cloud Run service URL>.
web: python backend.py
