# Operator-plane commands for the Gemini/Vertex integration.
# `make help` lists targets. Override vars inline, e.g. `make run PORT=5000`.

PROJECT  ?= evertune-tests
LOCATION ?= us-central1
# Cloud Run service name for `make deploy`
SERVICE  ?= evertune-backend
WEB_PORT     ?= 4454
BACKEND_PORT ?= 4460
# Where the UI proxies data calls. Defaults to the local backend; override with
# a Cloud Run URL to drive a deployed backend, e.g.
#   make start-web BACKEND_URL=https://evertune-backend-xxxx.run.app
BACKEND_URL  ?= http://127.0.0.1:$(BACKEND_PORT)

PYTHON  ?= python3          # bootstrap interpreter (should be 3.12+)
VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip

FMT_PATHS := llm backend.py web.py scripts

# Every target that touches the app gets project/location in its environment.
export GOOGLE_CLOUD_PROJECT  = $(PROJECT)
export GOOGLE_CLOUD_LOCATION = $(LOCATION)

.DEFAULT_GOAL := help
.PHONY: help setup format build backend web \
	start-backend-local stop-backend-local restart-backend-local \
	start-web stop-web restart-web \
	smoke probe shot runs test auth deploy status clean

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

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
	WEB_PORT=$(WEB_PORT) BACKEND_URL=$(BACKEND_URL) $(PY) web.py

start-backend-local: format ## Start the backend API in the background on $(BACKEND_PORT) (-> backend.log)
	@PORT=$(BACKEND_PORT) $(PY) backend.py > backend.log 2>&1 & \
		echo "backend  http://localhost:$(BACKEND_PORT) (pid $$!) -> backend.log"

stop-backend-local: ## Stop whatever is listening on $(BACKEND_PORT)
	@pids=$$(lsof -ti tcp:$(BACKEND_PORT)); \
		if [ -n "$$pids" ]; then kill $$pids && echo "backend stopped (pid $$pids)"; \
		else echo "backend: nothing on port $(BACKEND_PORT)"; fi

restart-backend-local: ## Restart the backend (stop, pause for the port to free, start)
	@$(MAKE) --no-print-directory stop-backend-local
	@sleep 1
	@$(MAKE) --no-print-directory start-backend-local

start-web: format ## Start the web/UI server in the background on $(WEB_PORT) (-> web.log)
	@WEB_PORT=$(WEB_PORT) BACKEND_URL=$(BACKEND_URL) $(PY) web.py > web.log 2>&1 & \
		echo "web      http://localhost:$(WEB_PORT) -> $(BACKEND_URL) (pid $$!) -> web.log"

stop-web: ## Stop whatever is listening on $(WEB_PORT)
	@pids=$$(lsof -ti tcp:$(WEB_PORT)); \
		if [ -n "$$pids" ]; then kill $$pids && echo "web stopped (pid $$pids)"; \
		else echo "web: nothing on port $(WEB_PORT)"; fi

restart-web: ## Restart the web server (stop, pause for the port to free, start)
	@$(MAKE) --no-print-directory stop-web
	@sleep 1
	@$(MAKE) --no-print-directory start-web

smoke: ## One-shot Vertex reachability check
	$(PY) scripts/smoke_test.py

shot: ## Screenshot the UI in a headless browser (server must be running)
	$(PY) scripts/ui_screenshot.py

runs: ## Summarize saved load-test runs from Atlas (P vs latency/errors)
	@$(PY) scripts/runs_report.py

test: ## Run the test suite
	$(PY) -m pytest -q

auth: ## Sign in Application Default Credentials — authenticates the Vertex/Gemini calls
	gcloud auth application-default login

deploy: build ## Deploy the backend to Cloud Run (source build). Uploads .env (Atlas creds) into the image; UI stays local — point it here with BACKEND_URL.
	@echo "Deploying backend to Cloud Run as '$(SERVICE)' in $(PROJECT)/$(LOCATION)."
	@echo "NOTE: .env (Atlas creds) IS uploaded into the image by design; the"
	@echo "service is public (--allow-unauthenticated) and spends Vertex \$$ per run."
	gcloud run deploy $(SERVICE) \
		--source . \
		--project $(PROJECT) \
		--region $(LOCATION) \
		--allow-unauthenticated \
		--set-env-vars GOOGLE_CLOUD_PROJECT=$(PROJECT),GOOGLE_CLOUD_LOCATION=$(LOCATION)
	@echo
	@echo "Run the UI against it:  make start-web BACKEND_URL=<service-url>"
	@echo "The service account needs roles/aiplatform.user to reach Vertex:"
	@echo "  gcloud projects add-iam-policy-binding $(PROJECT) \\"
	@echo "    --member=serviceAccount:<runtime-sa> --role=roles/aiplatform.user"

status: ## Print the resolved environment and whether the servers are up
	@echo "project   : $(PROJECT)"
	@echo "location  : $(LOCATION)"
	@echo "python    : $$($(PY) --version 2>/dev/null || echo 'no venv - run make setup')"
	@echo "gcloud acct: $$(gcloud config get-value account 2>/dev/null)"
	@pid=$$(lsof -ti tcp:$(BACKEND_PORT)); \
		echo "backend   : $(BACKEND_PORT) $$([ -n "$$pid" ] && echo "up (pid $$pid)" || echo down)"
	@pid=$$(lsof -ti tcp:$(WEB_PORT)); \
		echo "web       : $(WEB_PORT) $$([ -n "$$pid" ] && echo "up (pid $$pid)" || echo down)"

clean: ## Remove the venv
	rm -rf $(VENV)
	@echo "cleaned"
