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
| 1 | `data-prep` | Baixa ~15 GB de dados públicos (2 fotos da Receita, regimes tributários e 2 trimestres da PGFN), converte para Parquet e gera a coorte de treino e a tabela de empresas | ~45 min (≈32 min de download a ~5 MB/s) |
| 2 | `train` | Treina o modelo de sobrevivência e grava o artefato em `artifacts/model/` | ~2 min |
| 3 | `api` e `ui` | Sobem a API e a interface | segundos |

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
  features/      features por empresa numa data de referência e coorte de treino
  modeling/      modelo de sobrevivência: dados, candidatos, métricas, treino e artefato
  risk/          CNPJ, política de risco e avaliação (o relatório que o agent narra)
  api/           API HTTP (FastAPI) — porta de entrada do agent
  ui/            interface Streamlit (sem regra de negócio)
tests/           testes automatizados
notebooks/       análise exploratória e avaliação, executados (resultados visíveis no GitHub)
docs/adr/        registro das decisões de arquitetura
docker/          imagem única usada por todos os serviços
data/            dados baixados/processados (não versionado)
artifacts/       modelo treinado (não versionado; gerado pelo serviço train)
```

## Arquitetura

_(a preencher)_

## Decisões de modelagem e critério de risco aceitável

- **Pergunta modelada:** a probabilidade de o empregador sair da situação cadastral ativa
  (baixa, inaptidão, suspensão) até o fim do prazo do consignado, o que interrompe o desconto em
  folha. Ver [EDA](notebooks/01_eda.ipynb).
- **Modelo:** sobrevivência exponencial por partes com LightGBM (Poisson), treinado em 1,9 milhão
  de empresas com duas fotos da Receita (2023 → 2026). AUC de ~0,74 e risco calibrado até 36
  meses. Ver [modelagem](notebooks/02_modelagem.ipynb) e [ADR-0003](docs/adr/0003-modelo-de-sobrevivencia.md).
- **Risco aceitável:** até **10%** de chance de o empregador sair durante o prazo, com prazo máximo
  de **36 meses** (o horizonte validado). Configurável por `RISK_LIMITE_RISCO` e
  `RISK_PRAZO_MAXIMO_MESES`. Ver [política](notebooks/03_politica_de_risco.ipynb) e
  [ADR-0004](docs/adr/0004-politica-de-risco.md).

## Limitações e o que ficou de fora

_(a preencher)_

## Decisões registradas

Ver [docs/adr](docs/adr/README.md).
