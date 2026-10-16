# Imagem única para todos os serviços (data-prep, train, api e ui).
# Uma imagem só mantém as dependências idênticas entre treino e serving.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /uvx /bin/

# OpenMP: o LightGBM usa para treinar e prever em paralelo.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependências primeiro, para aproveitar o cache de camadas quando só o código muda.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# Sem USER fixo: `data/` e `artifacts/` são pastas do host montadas no contêiner, e um UID fixo
# (ex.: 1000) não conseguiria escrever nelas num Linux cujo usuário tem outro UID. Para uma
# imagem de produção, sem volumes do host, o certo é rodar com um usuário sem privilégios.
RUN mkdir -p /app/data /app/artifacts
