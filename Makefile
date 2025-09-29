SHELL := /bin/bash

# Absolute project root
PROJECT_ROOT := /home/aert14/city_fog_map

.PHONY: help up down build logs tunnel tunnel-status password kill clean tunnel-cf tunnel-cf-logs tunnel-cf-kill

help:
	@echo "Targets:"
	@echo "  up             - start all services with docker compose"
	@echo "  down           - stop all services"
	@echo "  build          - rebuild all services"
	@echo "  logs           - show logs from all services"
	@echo "  tunnel         - run LocalTunnel attached to :80 (default subdomain: aert0)"
	@echo "                 use SUBDOMAIN=foo to request custom subdomain"
	@echo "  tunnel-status  - check tunnel status and get URL"
	@echo "  password       - print LocalTunnel password (public IP)"
	@echo "  kill           - stop tunnel and clean temporary files"
	@echo "  tunnel-cf      - run Cloudflare tunnel using smart script"
	@echo "  tunnel-cf-logs - show Cloudflare tunnel logs (Ctrl+C to exit)"
	@echo "  tunnel-cf-kill - stop Cloudflare tunnel"
	@echo "  clean          - remove all containers and volumes"

up:
	@echo "Starting all services..."
	cd $(PROJECT_ROOT); docker compose up -d
	@echo "Services started. Web app available at http://localhost"
	@echo "RabbitMQ Management: http://localhost:15672 (guest/guest)"

down:
	@echo "Stopping all services..."
	cd $(PROJECT_ROOT); docker compose down

build:
	@echo "Building all services..."
	cd $(PROJECT_ROOT); docker compose build --no-cache

logs:
	cd $(PROJECT_ROOT); docker compose logs -f

tunnel:
	@pkill -f "localtunnel.*--port 80" || true
	@rm -f /tmp/lt.log /tmp/lt_url.txt
	@command -v npx >/dev/null 2>&1 || (echo "npx is required. Install Node: sudo apt-get install -y nodejs npm OR use nvm" && exit 1)
	@echo "Checking if port 80 is accessible..."
	@curl -s --max-time 5 http://localhost >/dev/null || (echo "ERROR: Port 80 is not accessible. Make sure services are running with 'make up'" && exit 1)
	@echo "Running localtunnel to :80 (attached)"
	@SUBDOMAIN=$${SUBDOMAIN:-aert0}; \
	echo "Using subdomain: $$SUBDOMAIN"; \
	npx --yes localtunnel --port 80 --local-host 127.0.0.1 -s "$$SUBDOMAIN" > /tmp/lt.log 2>&1 &
	@echo "Tunnel started in background. Use 'make tunnel-status' to check status."

tunnel-status:
	@if ps aux | grep -q "localtunnel.*--port 80" && [ -f /tmp/lt.log ]; then \
		URL=$$(grep -Eo "https://[a-z0-9-]+\\.loca\\.lt" /tmp/lt.log | head -n 1); \
		if [ -n "$$URL" ]; then \
			echo "Tunnel ready: $$URL"; \
			echo "$$URL" > /tmp/lt_url.txt; \
		else \
			echo "Tunnel starting... Check /tmp/lt.log for details."; \
			tail -5 /tmp/lt.log | head -3; \
		fi; \
	else \
		echo "No localtunnel process running."; \
		echo "Start tunnel with: make tunnel"; \
	fi

password:
	@curl -s https://loca.lt/mytunnelpassword; echo

kill:
	@echo "Stopping tunnel..."
	@pkill -f "localtunnel.*--port 80" || true
	@rm -f /tmp/lt.log /tmp/lt_url.txt
	@echo "Tunnel stopped and temporary files cleaned."

clean:
	@echo "Removing all containers and volumes..."
	cd $(PROJECT_ROOT); docker compose down -v --remove-orphans
	@echo "All containers and volumes removed."

tunnel-cf:
	@echo "Запуск управляющего скрипта для Cloudflare туннеля..."
	@./tools/run-cf-tunnel.sh

tunnel-cf-logs:
	@echo "Просмотр логов туннеля Cloudflare (нажмите Ctrl+C для выхода)..."
	@docker compose logs -f cloudflared

tunnel-cf-kill:
	@echo "Остановка туннеля Cloudflare..."
	@docker compose stop cloudflared
	@echo "Туннель остановлен."

