.PHONY: install dev dev-backend dev-frontend build start test

# Install all dependencies (backend + frontend)
install:
	cd backend && uv sync
	cd frontend && npm install

# Development mode: run backend and frontend in parallel (hot-reload)
# Both processes share the terminal; Ctrl-C stops both.
dev: dev-backend dev-frontend

dev-backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8787 &

dev-frontend:
	cd frontend && npm run dev & wait

# Build frontend and copy into backend/static/ for single-port production serving
build:
	cd frontend && npm run build
	rm -rf backend/static
	cp -r frontend/dist backend/static

# Production: build then serve API + UI on a single port (default: 8787)
start: build
	cd backend && uv run uvicorn app.main:app --port 8787

# Run backend tests and frontend lint + build
test:
	cd backend && uv run pytest
	cd frontend && npm run lint && npm run build
