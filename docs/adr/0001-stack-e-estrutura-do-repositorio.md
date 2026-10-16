# ADR-0001: Stack de engenharia e estrutura do repositório

- **Status:** Aceito
- **Data:** 2026-10-14

## Contexto

O sistema tem quatro partes: preparo de dados, treino de modelos, agent (servido por uma API) e
interface web. Ele precisa rodar com um único comando numa máquina que só tenha Docker, e quem
clonar o repositório deve conseguir treinar de novo e obter o mesmo modelo usado pelo agent.

## Decisão

- **Um único pacote Python** (`src/consig_risk`), com um subpacote por responsabilidade. Treino e
  serving importam o mesmo código de features, o que elimina diferença entre treino e uso real.
- **uv** para dependências, com `uv.lock` versionado: o ambiente é reproduzível bit a bit.
- **Uma única imagem Docker** para todos os serviços do `docker compose`; cada serviço muda só o
  comando. As dependências do treino e do serving são, por construção, as mesmas.
- **A UI só fala com a API.** A API é sem estado (o estado da conversa fica num session store), o que
  permite subir mais réplicas sem mudar código.
- **Qualidade automatizada:** ruff (lint e formatação), mypy em modo estrito, pytest com cobertura,
  pre-commit local e CI no GitHub Actions rodando os mesmos comandos (`make check`).
- **Configuração só por variáveis de ambiente** (pydantic-settings). A configuração do LLM fica
  separada (`LLMSettings`), porque preparo de dados e treino não precisam de chave.

## Alternativas consideradas

- **Pacotes/imagens separados por serviço:** imagens menores, mas abre espaço para versões diferentes
  de bibliotecas entre treino e serving. Para o tamanho do problema, o ganho não compensa.
- **Poetry/pip-tools:** funcionam, mas o uv é mais rápido e cobre instalação do Python, lock e
  execução com uma ferramenta só.
- **UI chamando o agent direto, sem API:** mais simples, mas acopla UI e agent no mesmo processo e
  impede escalar ou trocar a interface.

## Consequências

- A imagem única carrega dependências que alguns serviços não usam (ex.: a UI carrega bibliotecas de
  modelagem). Aceitável aqui; em produção, dividiríamos em imagens por serviço.
- Uma API sem estado exige um session store externo quando houver mais de uma réplica: hoje o ADK
  guarda as sessões em SQLite, e a troca para Postgres é só de URL. Ver o
  [ADR-0005](0005-caminho-para-escala.md).
- O contêiner roda sem usuário fixo porque `data/` e `artifacts/` são pastas do host montadas
  nele: um UID fixo não conseguiria escrever nelas num Linux cujo usuário tem outro UID. Uma
  imagem de produção, sem volumes do host, rodaria com usuário sem privilégios.
