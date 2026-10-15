# Risco de Consignado Privado — agent de análise por CNPJ

Sistema de IA que ajuda um analista de crédito a decidir se vale oferecer consignado privado aos
funcionários de uma empresa. O analista informa o CNPJ e conversa com um agent (Google ADK + LiteLLM)
que responde com uma análise de risco calculada em tempo real a partir de dados abertos da Receita
Federal e de modelos treinados neste repositório.

> **Status:** em construção. Seções marcadas com _(a preencher)_ serão completadas ao longo das etapas.

## Como executar

Pré-requisito: Docker (com Compose v2).

```bash
cp .env.example .env   # preencha LLM_MODEL e LLM_API_KEY
docker compose up --build
```

- Interface: http://localhost:8501
- API: http://localhost:8000/docs

O `docker compose up` roda as etapas em ordem; cada uma só começa quando a anterior termina:

| Etapa | Serviço | O que faz | Tempo na primeira vez |
|---|---|---|---|
| 1 | `data-prep` | Baixa ~15 GB de dados públicos (2 fotos da Receita, regimes tributários e 2 trimestres da PGFN) e converte para Parquet | ~40 min (≈32 min de download a ~5 MB/s e ~6 min de conversão) |
| 2 | `api` e `ui` | Sobem a API e a interface | segundos |

_(a preencher: treino do modelo)_

Todas as etapas são idempotentes: rodar de novo pula o que já está pronto, e um download
interrompido continua de onde parou. Os dados ficam em `data/` (cerca de 25 GB em disco).

Para rodar só o preparo de dados, fora do Docker:

```bash
uv run python -m consig_risk.data
```

## Desenvolvimento

Pré-requisito: [uv](https://docs.astral.sh/uv/).

```bash
make install   # dependências + hooks de pre-commit
make check     # lint + type check + testes (o mesmo que o CI)
```

## Estrutura do repositório

```
src/consig_risk/
  config.py      configuração via variáveis de ambiente
  data/          download das fontes públicas e conversão para Parquet
  api/           API HTTP (FastAPI) — porta de entrada do agent
  ui/            interface Streamlit (sem regra de negócio)
tests/           testes automatizados
notebooks/       análise exploratória e avaliação (resultados visíveis)
docs/adr/        registro das decisões de arquitetura
docker/          imagem única usada por todos os serviços
data/            dados baixados/processados (não versionado)
artifacts/       modelos treinados (não versionado)
```

## Arquitetura

_(a preencher)_

## Decisões de modelagem e critério de risco aceitável

_(a preencher)_

## Limitações e o que ficou de fora

_(a preencher)_

## Decisões registradas

Ver [docs/adr](docs/adr/README.md).
