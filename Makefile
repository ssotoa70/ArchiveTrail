.PHONY: install dev test lint clean docker-build docker-push sql-init help

PYTHON ?= python3
VENV   ?= .venv
IMAGE  ?= archive-trail
TAG    ?= latest

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Install package in editable mode with dev deps
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -e ".[dev]"

dev: ## Activate venv hint
	@echo "Run: source $(VENV)/bin/activate"

test: ## Run tests
	$(VENV)/bin/pytest tests/ -v

lint: ## Run ruff linter
	$(VENV)/bin/ruff check src/ tests/

lint-fix: ## Auto-fix lint issues
	$(VENV)/bin/ruff check --fix src/ tests/

clean: ## Remove build artifacts
	rm -rf build/ dist/ *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# -- Docker --

docker-build: ## Build DataEngine container
	docker build -t $(IMAGE):$(TAG) .

docker-push: ## Push container to registry (set REGISTRY env var)
	docker tag $(IMAGE):$(TAG) $(REGISTRY)/$(IMAGE):$(TAG)
	docker push $(REGISTRY)/$(IMAGE):$(TAG)

# -- Database --

sql-init: ## Display SQL init instructions
	@echo "Run the following SQL files in order against VAST DB:"
	@echo "  1. sql/001_create_schema.sql"
	@echo "  2. sql/002_create_tables.sql"
	@echo "  3. sql/003_seed_config.sql"
	@echo ""
	@echo "Use the VAST DB query editor in the management UI, or any Trino-compatible client."
	@echo "Example (using trino CLI):"
	@echo "  trino --catalog vast --execute \"\$$(cat sql/001_create_schema.sql)\""

# -- CLI shortcuts --

discover: ## Run discover (dry-run by default)
	$(VENV)/bin/python -m archive_trail discover

pipeline: ## Run full pipeline
	$(VENV)/bin/python -m archive_trail pipeline

stats: ## Show pipeline statistics
	$(VENV)/bin/python -m archive_trail stats

config: ## Show current config
	$(VENV)/bin/python -m archive_trail config list
