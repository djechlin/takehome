# Operator-plane commands for the Gemini/Vertex integration.
# `make help` lists targets. Override vars inline, e.g. `make run PORT=5000`.

PROJECT  ?= evertune-tests
LOCATION ?= us-central1
PORT     ?= 4454

PYTHON  ?= python3          # bootstrap interpreter (should be 3.12+)
VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip

FMT_PATHS := llm server.py scripts

# Every target that touches the app gets project/location in its environment.
export GOOGLE_CLOUD_PROJECT  = $(PROJECT)
export GOOGLE_CLOUD_LOCATION = $(LOCATION)

.DEFAULT_GOAL := help
.PHONY: help setup fmt run serve stop smoke probe shot runs test auth doctor clean

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Create the venv (3.12) and install requirements
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PIP) install --quiet -r requirements.txt
	@echo "venv ready: $$($(PY) --version)"

fmt: ## Format code with black (also fails fast on syntax errors)
	$(PY) -m black $(FMT_PATHS)

run: fmt ## Format, then run the web app in the foreground (Ctrl-C to stop)
	$(PY) server.py

serve: fmt ## Format, then start the web app in the background (writes server.log)
	@$(PY) server.py > server.log 2>&1 & echo "serving http://localhost:$(PORT) (pid $$!) -> server.log"

stop: ## Stop the background web app
	@pkill -f server.py && echo "stopped" || echo "nothing running"

smoke: ## One-shot Vertex reachability check
	$(PY) scripts/smoke_test.py

probe: ## Show per-request latency + hidden thinking-token usage
	$(PY) scripts/probe_thinking.py

shot: ## Screenshot the UI in a headless browser (server must be running)
	$(PY) scripts/ui_screenshot.py

runs: ## Summarize saved load-test runs from MongoDB (P vs latency/errors)
	@mongosh --quiet evertune_loadtest --eval 'db.run.find({}, {created_at:1, "config.n":1, "config.p":1, "aggregate.ok":1, "aggregate.error_count":1, "aggregate.wall_ms":1, "aggregate.throughput_rps":1, "aggregate.latency_ms":1}).sort({created_at:-1}).limit(20).forEach(r => print(`${r.created_at.toISOString()}  N=${r.config.n} P=${r.config.p}  ok=${r.aggregate.ok} err=${r.aggregate.error_count}  wall=${r.aggregate.wall_ms}ms  ${r.aggregate.throughput_rps}rps  p50/p95/p100=${r.aggregate.latency_ms.p50}/${r.aggregate.latency_ms.p95}/${r.aggregate.latency_ms.p100}ms`))'

test: ## Run the test suite
	$(PY) -m pytest -q

auth: ## Point gcloud ADC at the project (interactive)
	gcloud auth application-default login

doctor: ## Print the resolved environment
	@echo "project   : $(PROJECT)"
	@echo "location  : $(LOCATION)"
	@echo "port      : $(PORT)"
	@echo "python    : $$($(PY) --version 2>/dev/null || echo 'no venv - run make setup')"
	@echo "gcloud acct: $$(gcloud config get-value account 2>/dev/null)"

clean: ## Remove the venv and local run artifacts
	rm -rf $(VENV) server.log
	@echo "cleaned"
