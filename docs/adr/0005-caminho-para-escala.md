# ADR-0005: Como o sistema escala para uso interno

- **Status:** Aceito
- **Data:** 2026-10-16

## Contexto

O case não avalia desempenho sob muitas requisições, e o critério "solução do tamanho do
problema" pune excesso de infraestrutura. Mas, se o sistema virasse uma ferramenta interna usada
por vários analistas, algumas partes precisariam mudar. Este ADR registra o que já está preparado
para isso e o que mudaria, sem implementar o que o problema atual não pede.

## Decisão

Manter a forma atual (um `docker compose`, SQLite, Parquet, um processo de API) e deixar
**pontos de troca** explícitos no código, cada um atrás de uma interface ou de uma variável de
ambiente:

| Peça | Hoje | Ponto de troca | Em produção |
|---|---|---|---|
| Estado da conversa | `DatabaseSessionService` do ADK com SQLite | `SESSION_DB_URL` | Postgres; a API não guarda estado, então roda com várias réplicas atrás de um balanceador |
| Dados da empresa | Parquet ordenado por CNPJ, lido com DuckDB (~30–60 ms) | interface `CompanyRepository` | Tabela indexada por CNPJ básico num banco relacional ou num feature store, atualizada por job |
| Modelo | Artefato versionado em `artifacts/model` (árvores, categorias, métricas, hash da coorte) | `RiskModel.load` | Registro de modelos com versão por foto da Receita |
| Política | Variáveis `RISK_*` | `RiskPolicy` | Configuração versionada e auditável, aprovada pelo comitê de crédito |
| LLM | Qualquer provedor via LiteLLM | `LLM_*` | Gateway de LLM (ex.: LiteLLM proxy) com cota, cache e troca de provedor sem deploy |

## Gargalos, em ordem de impacto

1. **Latência e custo do LLM.** Cada mensagem faz duas chamadas ao LLM, e o resto do sistema leva
   cerca de 60 ms. Medido: de ~2 s (Gemini Flash) a ~2 min (endpoint gratuito com modelo de
   raciocínio). Caminhos: respostas em streaming (o ADK já tem `run_sse`); cache do relatório
   por (CNPJ, prazo, foto), que é determinístico; e um modelo menor só para decidir a ferramenta.
2. **Escrita de sessões.** O SQLite tem um único escritor. Com várias réplicas da API, a troca
   para Postgres é só de URL.
3. **Cálculo do risco dentro do loop assíncrono.** A ferramenta roda LightGBM e DuckDB de forma
   síncrona, o que bloqueia o loop por dezenas de milissegundos por chamada. Com carga alta, a
   ferramenta iria para um pool de threads, ou o cálculo para um serviço de scoring separado.
4. **Atualização mensal dos dados.** A Receita publica uma foto por mês. Isso viraria um job
   agendado: baixar a foto nova, reconstruir a tabela de empresas, monitorar a calibração com os
   desfechos novos e retreinar quando a calibração se desviar, sempre com o mesmo pipeline
   idempotente que existe hoje.

## O que mais mudaria num produto interno

- **Autenticação e auditoria:** login corporativo na API e registro de cada análise (CNPJ,
  prazo, veredito, versão do modelo e da política), porque é uma decisão de crédito.
- **Observabilidade:** métricas de latência por etapa, taxa de respostas com números sem origem
  (a checagem já existe) e rastreamento do ADK com OpenTelemetry.
- **Dados pessoais:** a razão social de empresário individual é o nome de uma pessoa. Hoje ela
  só aparece na tela do analista; num produto, entraria nas regras de retenção e de log.

## Alternativas consideradas

- **Implementar agora** Postgres, cache, fila e vários workers: aumentaria a superfície do projeto
  sem nenhum ganho para o problema do case.
- **Serviço de modelo separado (ex.: servidor de inferência):** é desnecessário com 60 ms por
  consulta; vale quando o modelo for compartilhado por outros sistemas.

## Consequências

- O sistema atende um analista por vez sem gargalo além do LLM.
- Cada troca listada acima é local: muda uma configuração ou uma implementação de interface, sem
  reescrever o agent, a política ou o modelo.
