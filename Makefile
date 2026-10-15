.PHONY: install lint format typecheck test check up down

install:  ## Instala dependências e hooks de pre-commit
	uv sync
	uv run pre-commit install

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy

test:
	uv run pytest -m "not llm"

check: lint typecheck test  ## O mesmo que o CI roda

up:
	docker compose up --build

down:
	docker compose down
