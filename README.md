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

_(a preencher: download e preparo dos dados, treino do modelo e tempos de cada etapa)_

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
