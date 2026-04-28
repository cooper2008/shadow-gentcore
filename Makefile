.PHONY: help setup lint typecheck test check smoke smoke-preflight smoke-quick agent-run workflow-run validate certify

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup: ## Install package in dev mode
	pip install -e ".[dev]"

lint: ## Run linter and type checker
	ruff check harness/ agents/ workflows/
	mypy harness/

typecheck: ## Run type checker without writing local cache
	mypy --cache-dir=/dev/null harness/

test: ## Run all tests
	pytest harness/tests/ -v

check: ## Run release quality checks without writing local pytest cache or bytecode
	ruff check --no-cache harness/ agents/ workflows/
	mypy --cache-dir=/dev/null harness/
	PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider harness/tests/ -q

smoke: ## Full smoke test (single + cross-domain, no API key)
	pytest harness/tests/smoke/ -v

smoke-preflight: ## Check all repos installed and accessible
	python -m harness.cli.ai test smoke --preflight

smoke-quick: ## Health check only on acme-backend
	python -m harness.cli.ai test smoke --domain ../acme-backend

agent-run: ## Run a single agent (AGENT=domain/AgentName)
	python -m harness.cli.ai run agent $(AGENT)

workflow-run: ## Run a workflow (WORKFLOW=path/to/workflow.yaml)
	python -m harness.cli.ai run workflow $(WORKFLOW)

validate: ## Validate manifests in a domain (DOMAIN=path/to/domain)
	python -m harness.cli.ai validate $(DOMAIN)

certify: ## Certify a domain (DOMAIN=path/to/domain)
	python -m harness.cli.ai certify $(DOMAIN)
