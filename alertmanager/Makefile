.PHONY: help install test run build clean docker-build docker-run format lint

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install dependencies with Poetry
	poetry install

test: ## Run tests
	poetry run pytest -v

test-cov: ## Run tests with coverage
	poetry run pytest --cov=app --cov-report=html --cov-report=term

run: ## Run the server locally
	poetry run python -m uvicorn app.main:app --reload --port 9093

format: ## Format code with ruff
	poetry run ruff format app tests

lint: ## Lint code with ruff
	poetry run ruff check app tests

lint-fix: ## Lint and fix code with ruff
	poetry run ruff check --fix app tests

docker-build: ## Build Docker image
	docker build -t ai-alertmanager:latest .

docker-run: ## Run Docker container
	docker run -p 9093:9093 \
		-e AI_ALERTMANAGER_HOLMES_URL=http://host.docker.internal:8080 \
		ai-alertmanager:latest

docker-compose-up: ## Start services with docker-compose
	docker-compose up -d

docker-compose-down: ## Stop services with docker-compose
	docker-compose down

k8s-deploy: ## Deploy to Kubernetes
	kubectl apply -f deployment.yaml

k8s-delete: ## Delete from Kubernetes
	kubectl delete -f deployment.yaml

k8s-logs: ## View logs from Kubernetes
	kubectl logs -n monitoring -l app=ai-alertmanager -f

clean: ## Clean build artifacts
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	rm -rf .pytest_cache/
	rm -rf .coverage
	rm -rf htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
