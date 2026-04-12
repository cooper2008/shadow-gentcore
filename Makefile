.PHONY: help setup lint test agent-run workflow-run validate certify

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup: ## Install package in dev mode
	pip install -e ".[dev]"

lint: ## Run linter and type checker
	ruff check harness/ agents/ workflows/
	mypy harness/

test: ## Run all tests
	pytest harness/tests/ -v

agent-run: ## Run a single agent (AGENT=domain/AgentName)
	python -m harness.cli.ai run agent $(AGENT)

workflow-run: ## Run a workflow (WORKFLOW=path/to/workflow.yaml)
	python -m harness.cli.ai run workflow $(WORKFLOW)

validate: ## Validate manifests in a domain (DOMAIN=path/to/domain)
	python -m harness.cli.ai validate $(DOMAIN)

certify: ## Certify a domain (DOMAIN=path/to/domain)
	python -m harness.cli.ai certify $(DOMAIN)
