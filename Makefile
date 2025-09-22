SHELL := /bin/bash

# Absolute project root
PROJECT_ROOT := ~/city_fog_map
VENV := $(PROJECT_ROOT)/.venv

.PHONY: help venv backend backend-debug backend-wo-auth tunnel tunnel-status password kill

help:
	@echo "Targets:"
	@echo "  venv           - create venv and install requirements"
	@echo "  backend        - run FastAPI (uvicorn) attached on :8000"
	@echo "  backend-debug  - run backend with DEBUG_AUTH_MODE=1 (auth demo only)"
	@echo "  tunnel         - run LocalTunnel attached to :8000 (use SUBDOMAIN=foo to request subdomain)"
	@echo "  tunnel-status  - check tunnel status and get URL"
	@echo "  password       - print LocalTunnel password (public IP)"
	@echo "  kill           - stop all project processes and clean temporary files"

venv:
	@test -d $(VENV) || python3 -m venv $(VENV)
	@source $(VENV)/bin/activate && pip install -r $(PROJECT_ROOT)/requirements.txt

backend: venv
	@test -n "$$TELEGRAM_BOT_TOKEN" || (echo "TELEGRAM_BOT_TOKEN is required" && exit 1)
	@echo "Running backend (uvicorn) on :8000 (attached)"
	cd $(PROJECT_ROOT); source $(VENV)/bin/activate; exec env TELEGRAM_BOT_TOKEN="$$TELEGRAM_BOT_TOKEN" uvicorn app.main:app --host 0.0.0.0 --port 8000

backend-debug: venv
backend-wo-auth: venv
	@test -n "$$TELEGRAM_BOT_TOKEN" || (echo "TELEGRAM_BOT_TOKEN is required" && exit 1)
	@echo "Running backend with NO_AUTH_MODE=1 on :8000 (attached)"
	cd $(PROJECT_ROOT); source $(VENV)/bin/activate; exec env NO_AUTH_MODE=1 TELEGRAM_BOT_TOKEN="$$TELEGRAM_BOT_TOKEN" uvicorn app.main:app --host 0.0.0.0 --port 8000
	@echo "Running backend in DEBUG_AUTH_MODE=1 on :8000 (attached)"
	cd $(PROJECT_ROOT); source $(VENV)/bin/activate; exec env DEBUG_AUTH_MODE=1 TELEGRAM_BOT_TOKEN="$$TELEGRAM_BOT_TOKEN" uvicorn app.main:app --host 0.0.0.0 --port 8000

tunnel:
	@pkill -f "localtunnel --port 8000" || true
	@rm -f /tmp/lt.log /tmp/lt_url.txt
	@command -v npx >/dev/null 2>&1 || (echo "npx is required. Install Node: sudo apt-get install -y nodejs npm OR use nvm" && exit 1)
	@echo "Running localtunnel to :8000 (attached)"
	@if [ -n "$$SUBDOMAIN" ]; then \
		echo "Requesting subdomain: $$SUBDOMAIN"; \
		npx --yes localtunnel --port 8000 --local-host 127.0.0.1 -s "$$SUBDOMAIN" > /tmp/lt.log 2>&1 & \
	else \
		npx --yes localtunnel --port 8000 --local-host 127.0.0.1 > /tmp/lt.log 2>&1 & \
	fi
	@echo "Tunnel started in background. Use 'make tunnel-status' to check status."

tunnel-status:
	@URL=$$(grep -Eo "https://[a-z0-9-]+\\.loca\\.lt" /tmp/lt.log | head -n 1); \
	if [ -n "$$URL" ]; then \
		echo "Tunnel ready: $$URL"; \
		echo "$$URL" > /tmp/lt_url.txt; \
	else \
		echo "Tunnel not ready yet. Check /tmp/lt.log for details."; \
		ps aux | grep localtunnel | grep -v grep || echo "No localtunnel process running."; \
	fi

password:
	@curl -s https://loca.lt/mytunnelpassword; echo

kill:
	@echo "Stopping all project processes..."
	@pkill -f "uvicorn.*app.main:app" || true
	@pkill -f "localtunnel.*--port 8000" || true
	@rm -f /tmp/lt.log /tmp/lt_url.txt
	@echo "Checking if port 8000 is free..."
	@lsof -i :8000 >/dev/null 2>&1 || echo "Port 8000 is free"
	@echo "All processes stopped and temporary files cleaned."
	@exit 0

