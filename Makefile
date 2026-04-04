.PHONY: install dev test lint clean help \
       build-discover build-offload build-verify-purge build-all \
       push-discover push-offload push-verify-purge push-all \
       prep-functions

PYTHON   ?= python3
VENV     ?= .venv
REGISTRY ?= your-registry.example.com:5000
VERSION  ?= $(shell date +%Y%m%d-%H%M%S)

# Function names
FUNC_DISCOVER     = archive-trail-discover
FUNC_OFFLOAD      = archive-trail-offload
FUNC_VERIFY_PURGE = archive-trail-verify-purge

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# -- Development --

install: ## Install package in editable mode with dev deps
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -e ".[dev]"

dev: ## Activate venv hint
	@echo "Run: source $(VENV)/bin/activate"

test: ## Run tests
	$(VENV)/bin/pytest tests/ -v

lint: ## Run ruff linter
	$(VENV)/bin/ruff check src/ tests/ functions/

lint-fix: ## Auto-fix lint issues
	$(VENV)/bin/ruff check --fix src/ tests/ functions/

clean: ## Remove build artifacts
	rm -rf build/ dist/ *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf functions/discover/archive_trail/
	rm -rf functions/offload/archive_trail/
	rm -rf functions/verify_purge/archive_trail/

# -- Prepare functions (copy shared library into each function dir) --

prep-functions: ## Copy shared library into function directories for build
	@for func in discover offload verify_purge; do \
		rm -rf functions/$$func/archive_trail; \
		cp -r src/archive_trail functions/$$func/archive_trail; \
		echo "Copied archive_trail -> functions/$$func/"; \
	done

# -- Build (CNB + Dockerfile.fix) --

build-discover: prep-functions ## Build discover function image
	vastde functions build $(FUNC_DISCOVER) \
		--target functions/discover --pull-policy never
	docker build --platform linux/amd64 --no-cache \
		--build-arg BASE_IMAGE=$(FUNC_DISCOVER):latest \
		-t $(REGISTRY)/$(FUNC_DISCOVER):$(VERSION) \
		-f Dockerfile.fix .

build-offload: prep-functions ## Build offload function image
	vastde functions build $(FUNC_OFFLOAD) \
		--target functions/offload --pull-policy never
	docker build --platform linux/amd64 --no-cache \
		--build-arg BASE_IMAGE=$(FUNC_OFFLOAD):latest \
		-t $(REGISTRY)/$(FUNC_OFFLOAD):$(VERSION) \
		-f Dockerfile.fix .

build-verify-purge: prep-functions ## Build verify_purge function image
	vastde functions build $(FUNC_VERIFY_PURGE) \
		--target functions/verify_purge --pull-policy never
	docker build --platform linux/amd64 --no-cache \
		--build-arg BASE_IMAGE=$(FUNC_VERIFY_PURGE):latest \
		-t $(REGISTRY)/$(FUNC_VERIFY_PURGE):$(VERSION) \
		-f Dockerfile.fix .

build-all: build-discover build-offload build-verify-purge ## Build all function images

# -- Push --

push-discover: ## Push discover image to registry
	docker push $(REGISTRY)/$(FUNC_DISCOVER):$(VERSION)

push-offload: ## Push offload image to registry
	docker push $(REGISTRY)/$(FUNC_OFFLOAD):$(VERSION)

push-verify-purge: ## Push verify_purge image to registry
	docker push $(REGISTRY)/$(FUNC_VERIFY_PURGE):$(VERSION)

push-all: push-discover push-offload push-verify-purge ## Push all images

# -- Deploy (create/update DataEngine functions) --

deploy-discover: ## Create or update discover function in DataEngine
	vastde functions create --name $(FUNC_DISCOVER) \
		--container-registry my-registry \
		--artifact-source $(FUNC_DISCOVER) \
		--image-tag $(VERSION) 2>/dev/null || \
	vastde functions update $(FUNC_DISCOVER) --image-tag $(VERSION)

deploy-offload: ## Create or update offload function
	vastde functions create --name $(FUNC_OFFLOAD) \
		--container-registry my-registry \
		--artifact-source $(FUNC_OFFLOAD) \
		--image-tag $(VERSION) 2>/dev/null || \
	vastde functions update $(FUNC_OFFLOAD) --image-tag $(VERSION)

deploy-verify-purge: ## Create or update verify_purge function
	vastde functions create --name $(FUNC_VERIFY_PURGE) \
		--container-registry my-registry \
		--artifact-source $(FUNC_VERIFY_PURGE) \
		--image-tag $(VERSION) 2>/dev/null || \
	vastde functions update $(FUNC_VERIFY_PURGE) --image-tag $(VERSION)

deploy-all: deploy-discover deploy-offload deploy-verify-purge ## Deploy all functions

# -- Full workflow --

ship: build-all push-all deploy-all ## Build, push, and deploy all functions
	@echo "All functions deployed with tag: $(VERSION)"
	@echo "Create the pipeline via VMS UI:"
	@echo "  Schedule Trigger -> $(FUNC_DISCOVER) -> $(FUNC_OFFLOAD) -> $(FUNC_VERIFY_PURGE)"

# -- CLI shortcuts (local development) --

discover: ## Run discover locally (dry-run by default)
	$(VENV)/bin/python -m archive_trail discover

pipeline: ## Run full pipeline locally
	$(VENV)/bin/python -m archive_trail pipeline

stats: ## Show pipeline statistics
	$(VENV)/bin/python -m archive_trail stats

config: ## Show current config
	$(VENV)/bin/python -m archive_trail config list
