.PHONY: help build publish release-check test test-core test-integration lint lint-core docs docs-serve api-start

.DEFAULT_GOAL := help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-16s\033[0m %s\n", $$1, $$2}'

build: ## Build and validate wheel and source distributions
	rm -rf dist/
	uv build
	uv run --with twine twine check dist/*

release-check: lint test docs build ## Run all release checks

publish: release-check ## Validate and publish the package to PyPI
	@export $$(grep UV_PUBLISH_TOKEN .env | xargs) && uv publish

test: ## Run the test suite
	uv run pytest tests

test-core: ## Run the test suite (alias of test)
	uv run pytest tests

test-integration: ## Run integration-marked tests
	uv run pytest --no-cov -m integration

lint: ## Check lint and formatting
	uv run ruff check src tests
	uv run ruff format --check src tests

lint-core: ## Check lint and formatting (alias of lint)
	uv run ruff check src tests && uv run ruff format --check src tests

docs: ## Build the docs site
	uv run mkdocs build --strict

docs-serve: ## Serve docs locally at localhost:8001
	uv run mkdocs serve -a localhost:8001

api-start: ## Start the basemode-loom web API (grove backend)
	uv run basemode-loom serve
