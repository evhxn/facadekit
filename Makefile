.PHONY: setup lint test build-web dev run run-sim

setup:
	cd studio/backend && uv sync --all-extras
	@if [ -f studio/web/package.json ]; then cd studio/web && pnpm install; fi

lint:
	cd studio/backend && uv run ruff check . && uv run ruff format --check . && uv run mypy src
	@if [ -f studio/web/package.json ]; then cd studio/web && pnpm lint && pnpm typecheck; fi

test:
	cd studio/backend && uv run pytest -q
	@if [ -f studio/web/package.json ]; then cd studio/web && pnpm test; fi

build-web:
	cd studio/web && pnpm build

run:
	cd studio/backend && uv run facadekit serve

run-sim:
	cd studio/backend && uv run facadekit serve --sim

dev:
	@echo "Run these in two terminals:"
	@echo "  cd studio/backend && uv run facadekit serve --no-browser"
	@echo "  cd studio/web && pnpm dev"
