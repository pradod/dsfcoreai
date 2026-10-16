# ADR-0003: Modelo de sobrevivência exponencial por partes com LightGBM

- **Status:** Aceito
- **Data:** 2026-10-15

## Contexto

O agent precisa responder, para qualquer CNPJ e prazo, a probabilidade de o empregador continuar
ativo até o fim do crédito. Isso inclui o veredito para um prazo informado, o prazo máximo
aceitável e perguntas de acompanhamento ("e se fossem 12 meses?"), sempre coerentes entre si.

A EDA ([notebooks/01_eda.ipynb](../../notebooks/01_eda.ipynb)) trouxe três fatos que moldam o
modelo:

1. As inaptidões chegam em **lotes anuais** da Receita. A data do evento é administrativa, e o
   calendário de 2023–2026 (sem lote em 2025) não vale para um empréstimo que começa hoje.
2. As curvas de sobrevivência por idade **se cruzam**: o efeito das features muda ao longo do prazo.
3. O acompanhamento é de 41 meses, mas os prazos de consignado vão além disso.

## Decisão

**Análise de sobrevivência, não um classificador por prazo.** Uma única curva S(t) por empresa
gera o veredito para qualquer prazo, o prazo máximo e as respostas de acompanhamento sem
contradições.

**Modelo exponencial por partes (piecewise exponential).** O acompanhamento de cada empresa é
dividido em ano 1, ano 2 e "ano 3 em diante". Em cada período, o risco é uma taxa mensal que
depende das features da empresa, do período e da **idade da empresa no início do período**.

- O calendário dos lotes vira uma média dentro de cada período.
- O efeito das features pode variar entre períodos e com a idade.
- Para prazos acima de 41 meses, cada novo ano repete o último período com a idade atualizada.

**Estimação com LightGBM, objetivo Poisson**, com alvo `eventos / exposição` e peso `exposição`
(equivalente a log(exposição) como offset). As explicações por empresa usam as contribuições
SHAP nativas do LightGBM.

**Dados e reprodutibilidade.** Splits por balde fixo do hash do CNPJ: treino 20% (1,9 mi
empresas), validação 5% (parada antecipada) e teste 10% (só para o resultado final). Treino
determinístico (`deterministic`, `force_row_wise`, semente e número de threads fixos): duas
execuções na mesma plataforma geram o mesmo arquivo de modelo, byte a byte. Entre plataformas
(macOS ARM e o contêiner Linux do `docker compose`), o arquivo difere apenas na formatação textual
de alguns números, e as previsões são idênticas (diferença máxima de 0,0 em S(t) para 95 mil
empresas).

## Resultados

No conjunto de teste (954 mil empresas), com detalhes em
[notebooks/02_modelagem.ipynb](../../notebooks/02_modelagem.ipynb):

| Horizonte | AUC | Risco previsto | Observado |
|---|---|---|---|
| 12 meses | 0,743 | 6,7% | 6,6% |
| 24 meses | 0,745 | 13,1% | 12,9% |
| 36 meses | 0,730 | 21,1% | 16,4% |
| 41 meses | 0,751 | 24,0% | 24,3% |

O desvio em 36 meses é o efeito do calendário: até o mês 36 a janela observada teve só dois dos
três lotes anuais de inaptidão. Aos 41 meses, com o calendário completo, o previsto bate com o
observado. A AUC é de cerca de 0,82 nas empresas médias e grandes e de 0,63–0,66 nas com menos de
3 anos.

## Alternativas consideradas

Todas treinadas nos mesmos 5% de empresas e avaliadas na validação:

| Modelo | C-index | AUC 24m | Previsto/observado 41m | Por que não |
|---|---|---|---|---|
| Kaplan-Meier | 0,50 | 0,50 | 24,3% / 24,3% | Não distingue empresas; é o piso |
| Cox de riscos proporcionais | 0,67 | 0,68 | 20,1% / 24,3% | Separa menos, subestima o risco, absorve o calendário dos lotes na curva base e não extrapola com a idade |
| Exponencial por partes + GLM de Poisson | 0,69 | 0,70 | 24,1% / 24,3% | Bem calibrado e interpretável, mas separa menos |
| **Exponencial por partes + LightGBM** | **0,72** | **0,74** | **24,0% / 24,3%** | Escolhido |

Também foram avaliados:

- **Sem as fontes complementares:** só com dados da Receita, a AUC em 24 meses cai de 0,74 para
  0,71. ECF e PGFN acrescentam cerca de 0,02 e 0,015 cada.
- **Evento "só baixada":** o risco cai para menos da metade e a AUC para 0,69. A baixa voluntária
  é mais difícil de antecipar que o abandono (inaptidão), que deixa rastros fiscais.
- **Taxa de aprendizado 0,05:** chega ao limite de 2.000 árvores com a mesma métrica que 0,1
  (diferença de AUC de 0,0002); 0,1 converge em ~1.100 árvores e treina na metade do tempo.

## Consequências

- O risco acima de 36 meses é **extrapolado**, sem validação direta. O agent precisa dizer isso.
- O modelo distingue pouco entre empresas com menos de 3 anos. O agent precisa avisar.
- Valores muito acima da faixa do treino (capital de centenas de bilhões, milhares de filiais)
  são tratados como o maior valor visto. O agent sinaliza esses casos.
- Uma AUC em torno de 0,74 é modesta, mas esperada sem dados de faturamento, folha ou histórico
  de crédito. O veredito se apoia na **probabilidade calibrada**, que está correta nos horizontes
  validáveis e por segmento.
