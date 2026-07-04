# Operator-plane commands for the Gemini/Vertex integration.
# `make help` lists targets. Override vars inline, e.g. `make run PORT=5000`.

PROJECT  ?= evertune-tests
LOCATION ?= us-central1
PORT     ?= 4454

PYTHON  ?= python3          # bootstrap interpreter (should be 3.12+)
VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip

# Every target that touches the app gets project/location in its environment.
export GOOGLE_CLOUD_PROJECT  = $(PROJECT)
export GOOGLE_CLOUD_LOCATION = $(LOCATION)

.DEFAULT_GOAL := help
.PHONY: help setup run serve stop smoke test auth doctor clean

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Create the venv (3.12) and install requirements
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PIP) install --quiet -r requirements.txt
	@echo "venv ready: $$($(PY) --version)"

run: ## Run the web app in the foreground (Ctrl-C to stop)
	$(PY) server.py

serve: ## Start the web app in the background (writes server.log)
	@$(PY) server.py > server.log 2>&1 & echo "serving http://localhost:$(PORT) (pid $$!) -> server.log"

stop: ## Stop the background web app
	@pkill -f server.py && echo "stopped" || echo "nothing running"

smoke: ## One-shot Vertex reachability check
	$(PY) scripts/smoke_test.py

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
