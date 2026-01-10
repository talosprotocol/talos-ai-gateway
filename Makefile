.PHONY: up down test lint typecheck migrate

up:
	docker-compose up -d

down:
	docker-compose down

dev:
	uvicorn app.main:app --reload --port 8000

test:
	pytest tests/

lint:
	ruff check app/ tests/

typecheck:
	mypy app/

itest:
	docker-compose up -d
	pytest tests/integration/
	docker-compose down

migrate:
	alembic upgrade head

migrate-down:
	alembic downgrade -1

migrate-new:
	alembic revision --autogenerate -m "$(MSG)"
