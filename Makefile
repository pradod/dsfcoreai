.PHONY: install lint format typecheck test test-llm check up down data notebooks

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
	uv run pytest

check: lint typecheck test  ## O mesmo que o CI roda

up:
	docker compose up --build

down:
	docker compose down

data:  ## Baixa e prepara os dados (idempotente; ~45 min na primeira vez)
	uv run python -m consig_risk.data

notebooks:  ## Reexecuta os notebooks, gravando os resultados neles
	uv run --group notebooks jupyter nbconvert --to notebook --execute --inplace \
		--ExecutePreprocessor.timeout=1800 notebooks/*.ipynb

test-llm:  ## Avalia o agent com o LLM do .env sobre os dados reais (custa chamadas)
	uv run pytest -m llm --no-cov
