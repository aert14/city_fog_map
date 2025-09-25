SHELL := /bin/bash

# Absolute project root
PROJECT_ROOT := ~/city_fog_map

.PHONY: help up down build logs tunnel tunnel-status password kill clean

help:
	@echo "Targets:"
	@echo "  up             - start all services with docker-compose"
	@echo "  down           - stop all services"
	@echo "  build          - rebuild all services"
	@echo "  logs           - show logs from all services"
	@echo "  tunnel         - run LocalTunnel attached to :80 (default subdomain: aert0)"
	@echo "                 use SUBDOMAIN=foo to request custom subdomain"
	@echo "  tunnel-status  - check tunnel status and get URL"
	@echo "  password       - print LocalTunnel password (public IP)"
	@echo "  kill           - stop tunnel and clean temporary files"
	@echo "  clean          - remove all containers and volumes"

up:
	@echo "Starting all services..."
	cd $(PROJECT_ROOT); docker-compose up -d
	@echo "Services started. Web app available at http://localhost"
	@echo "RabbitMQ Management: http://localhost:15672 (guest/guest)"

down:
	@echo "Stopping all services..."
	cd $(PROJECT_ROOT); docker-compose down

build:
	@echo "Building all services..."
	cd $(PROJECT_ROOT); docker-compose build --no-cache

logs:
	cd $(PROJECT_ROOT); docker-compose logs -f

tunnel:
	@pkill -f "localtunnel --port 80" || true
	@rm -f /tmp/lt.log /tmp/lt_url.txt
	@command -v npx >/dev/null 2>&1 || (echo "npx is required. Install Node: sudo apt-get install -y nodejs npm OR use nvm" && exit 1)
	@echo "Running localtunnel to :80 (attached)"
	@if [ -n "$$SUBDOMAIN" ]; then \
		echo "Requesting subdomain: $$SUBDOMAIN"; \
		SUBDOMAIN="$$SUBDOMAIN"; \
	else \
		echo "Using default subdomain: aert0"; \
		SUBDOMAIN="aert0"; \
	fi; \
	npx --yes localtunnel --port 80 --local-host 127.0.0.1 -s "$$SUBDOMAIN" > /tmp/lt.log 2>&1 &
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
	@echo "Stopping tunnel..."
	@pkill -f "localtunnel.*--port 80" || true
	@rm -f /tmp/lt.log /tmp/lt_url.txt
	@echo "Checking if port 80 is free..."
	@lsof -i :80 >/dev/null 2>&1 || echo "Port 80 is free"
	@echo "Tunnel stopped and temporary files cleaned."
	@exit 0

clean:
	@echo "Removing all containers and volumes..."
	cd $(PROJECT_ROOT); docker-compose down -v --remove-orphans
	@echo "All containers and volumes removed."

