.PHONY: help install dev web api mcp db-init logs kill wait-api

# ── Config ────────────────────────────────────────────────────────────────────
API_PORT   ?= 8080
API_HOST   ?= 127.0.0.1
WEB_PORT   ?= 5005
LOG_DIR    := .logs

# 清掉失效本地代理，避免 AkShare/同花顺请求打到死掉的 127.0.0.1:xxxxx
export NO_PROXY := 127.0.0.1,localhost
unexport http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy

# ── Help ──────────────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "  XShare — service management"
	@echo ""
	@echo "  make install       Install Python + Node deps"
	@echo "  make db-init       Init DuckDB + SQLite schema (run once)"
	@echo ""
	@echo "  make dev           Start API + frontend (background, logs in $(LOG_DIR)/)"
	@echo "  make api           Start FastAPI REST server only"
	@echo "  make mcp           Start MCP Server (stdio)"
	@echo "  make web           Start Next.js dev server only"
	@echo ""
	@echo "  Sync jobs: use Web UI /sync (not Makefile/CLI)"
	@echo ""
	@echo "  make logs          Tail all background logs"
	@echo "  make kill          Stop all background XShare processes"
	@echo ""

# ── Setup ─────────────────────────────────────────────────────────────────────
install:
	uv sync --dev
	cd frontend && npm install

db-init:
	uv run xshare db init

# ── Services ──────────────────────────────────────────────────────────────────

## Start FastAPI REST server (foreground)
api:
	uv run xshare web --host $(API_HOST) --port $(API_PORT)

## Start MCP Server (foreground, stdio transport)
mcp:
	uv run xshare serve

## Start Next.js dev server (foreground)
web:
	cd frontend && npm run dev -- --port $(WEB_PORT)

## Start API + frontend in background, writing to $(LOG_DIR)/
dev: kill $(LOG_DIR)
	@echo "Starting API server on http://$(API_HOST):$(API_PORT) ..."
	@bash -c 'env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy \
		nohup uv run xshare web --host $(API_HOST) --port $(API_PORT) \
		> $(LOG_DIR)/api.log 2>&1 </dev/null & echo $$! > $(LOG_DIR)/api.pid; disown 2>/dev/null || true'
	@echo "Starting frontend on http://$(API_HOST):$(WEB_PORT) ..."
	@bash -c 'env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy \
		nohup npm --prefix frontend run dev -- --port $(WEB_PORT) \
		> $(LOG_DIR)/front.log 2>&1 </dev/null & echo $$! > $(LOG_DIR)/front.pid; disown 2>/dev/null || true'
	@$(MAKE) --no-print-directory wait-api
	@# 用实际监听端口的进程覆盖 pid（uv/npm 包装进程可能已退出）
	@lsof -tiTCP:$(API_PORT) -sTCP:LISTEN 2>/dev/null | head -1 > $(LOG_DIR)/api.pid || true
	@lsof -tiTCP:$(WEB_PORT) -sTCP:LISTEN 2>/dev/null | head -1 > $(LOG_DIR)/front.pid || true
	@echo ""
	@echo "  API:      http://$(API_HOST):$(API_PORT)  ($(LOG_DIR)/api.log)"
	@echo "  Docs:     http://$(API_HOST):$(API_PORT)/docs"
	@echo "  Frontend: http://$(API_HOST):$(WEB_PORT)   ($(LOG_DIR)/front.log)"
	@echo "  Sync UI:  http://$(API_HOST):$(WEB_PORT)/sync"
	@echo ""
	@echo "  Run 'make logs' to tail logs, 'make kill' to stop."

wait-api:
	@ok=0; \
	for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do \
		if curl -sf "http://$(API_HOST):$(API_PORT)/api/health" >/dev/null 2>&1; then \
			echo "API ready ($$i s)"; ok=1; break; \
		fi; \
		sleep 1; \
	done; \
	if [ "$$ok" != "1" ]; then \
		echo "API failed to become ready. Last log:"; \
		tail -n 40 $(LOG_DIR)/api.log 2>/dev/null || true; \
		exit 1; \
	fi

$(LOG_DIR):
	@mkdir -p $(LOG_DIR)

# ── Utilities ─────────────────────────────────────────────────────────────────
logs:
	@tail -f $(LOG_DIR)/*.log 2>/dev/null || echo "No log files found in $(LOG_DIR)/"

kill:
	@for f in $(LOG_DIR)/*.pid; do \
		[ -f "$$f" ] || continue; \
		pid=$$(cat "$$f"); \
		name=$$(basename "$$f" .pid); \
		if kill -0 "$$pid" 2>/dev/null; then \
			kill "$$pid" 2>/dev/null || true; \
			kill -- -"$$pid" 2>/dev/null || true; \
			echo "Stopped $$name (pid $$pid)"; \
		else \
			echo "$$name (pid $$pid) already stopped"; \
		fi; \
		rm -f "$$f"; \
	done
	@for port in $(API_PORT) $(WEB_PORT) 3000; do \
		pids=$$(lsof -ti :$$port 2>/dev/null); \
		if [ -n "$$pids" ]; then \
			echo "Killing leftover process(es) on port $$port: $$pids"; \
			echo "$$pids" | xargs kill 2>/dev/null || true; \
			sleep 0.3; \
			pids=$$(lsof -ti :$$port 2>/dev/null); \
			if [ -n "$$pids" ]; then \
				echo "$$pids" | xargs kill -9 2>/dev/null || true; \
			fi; \
		fi; \
	done
	@pkill -f "next dev" 2>/dev/null && echo "Stopped next dev process(es)" || true
	@pkill -f "xshare web" 2>/dev/null || true
	@pkill -f "uvicorn xshare.web_server" 2>/dev/null || true
	@echo "Done."
