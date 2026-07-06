# Operator-plane commands for the Gemini/Vertex integration.
# `make help` lists targets. Override vars inline, e.g. `make run PORT=5000`.

PROJECT  ?= evertune-tests
LOCATION ?= us-central1
WEB_PORT     ?= 4454
BACKEND_PORT ?= 8080

PYTHON  ?= python3          # bootstrap interpreter (should be 3.12+)
VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip

FMT_PATHS := llm backend.py web.py scripts

# Every target that touches the app gets project/location in its environment.
export GOOGLE_CLOUD_PROJECT  = $(PROJECT)
export GOOGLE_CLOUD_LOCATION = $(LOCATION)

.DEFAULT_GOAL := help
.PHONY: help setup format build backend web serve stop smoke probe shot runs test auth doctor clean

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Create the venv (3.12) and install requirements
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PIP) install --quiet -r requirements.txt
	@echo "venv ready: $$($(PY) --version)"

format: ## Format code with black (also fails fast on syntax errors)
	$(PY) -m black $(FMT_PATHS)

build: format ## Format, then byte-compile every source file (fails on any syntax/import-time error)
	$(PY) -m compileall -q $(FMT_PATHS)
	@echo "build ok"

backend: format ## Format, then run the backend API in the foreground (Ctrl-C to stop)
	PORT=$(BACKEND_PORT) $(PY) backend.py

web: format ## Format, then run the web/UI server in the foreground (proxies to backend)
	WEB_PORT=$(WEB_PORT) BACKEND_URL=http://127.0.0.1:$(BACKEND_PORT) $(PY) web.py

serve: format ## Start backend + web in the background (writes backend.log / web.log)
	@PORT=$(BACKEND_PORT) $(PY) backend.py > backend.log 2>&1 & echo "backend  http://localhost:$(BACKEND_PORT) (pid $$!) -> backend.log"
	@WEB_PORT=$(WEB_PORT) BACKEND_URL=http://127.0.0.1:$(BACKEND_PORT) $(PY) web.py > web.log 2>&1 & echo "web      http://localhost:$(WEB_PORT) (pid $$!) -> web.log"

stop: ## Stop the background backend + web
	@pkill -f backend.py && echo "backend stopped" || echo "backend: nothing running"
	@pkill -f web.py && echo "web stopped" || echo "web: nothing running"

smoke: ## One-shot Vertex reachability check
	$(PY) scripts/smoke_test.py

shot: ## Screenshot the UI in a headless browser (server must be running)
	$(PY) scripts/ui_screenshot.py

runs: ## Summarize saved load-test runs from MongoDB (P vs latency/errors)
	@mongosh --quiet evertune_loadtest --eval 'db.run.find({}, {created_at:1, "config.n":1, "config.p":1, "aggregate.ok":1, "aggregate.error_count":1, "aggregate.wall_ms":1, "aggregate.throughput_rps":1, "aggregate.latency_ms":1}).sort({created_at:-1}).limit(20).forEach(r => print(`${r.created_at.toISOString()}  N=${r.config.n} P=${r.config.p}  ok=${r.aggregate.ok} err=${r.aggregate.error_count}  wall=${r.aggregate.wall_ms}ms  ${r.aggregate.throughput_rps}rps  p50/p95/p100=${r.aggregate.latency_ms.p50}/${r.aggregate.latency_ms.p95}/${r.aggregate.latency_ms.p100}ms`))'

test: ## Run the test suite
	$(PY) -m pytest -q

auth: ## Sign in Application Default Credentials — authenticates the Vertex/Gemini calls
	gcloud auth application-default login

doctor: ## Print the resolved environment
	@echo "project   : $(PROJECT)"
	@echo "location  : $(LOCATION)"
	@echo "port      : $(PORT)"
	@echo "python    : $$($(PY) --version 2>/dev/null || echo 'no venv - run make setup')"
	@echo "gcloud acct: $$(gcloud config get-value account 2>/dev/null)"

clean: ## Remove the venv
	rm -rf $(VENV)
	@echo "cleaned"
