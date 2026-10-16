# ADR-0004: Política de risco aceitável

- **Status:** Aceito
- **Data:** 2026-10-15

## Contexto

O case pede que se defina o que é risco aceitável e como ele vira um veredito. O modelo
(ADR-0003) entrega, para cada empresa e prazo, a probabilidade de o empregador sair da situação
cadastral ativa até o fim do prazo.

Quando o empregador sai, o desconto em folha para. Isso não é calote automático: o funcionário
pode continuar pagando, levar a dívida para o novo empregador ou acionar a garantia do FGTS. O que
se perde é o canal de desconto, que é a garantia que torna o consignado mais barato que o crédito
pessoal. A política limita a chance de perder esse canal durante o prazo.

Referências do produto: o Banco Central não impõe prazo máximo ao consignado privado, e o mercado
costuma ir até cerca de 48 meses. A taxa média da modalidade ficou em torno de 56% ao ano em 2025.

## Decisão

**Regra:** conceder um consignado de *t* meses se P(empregador sair até *t*) ≤ **10%**.

**Prazo máximo:** o maior prazo, numa grade de 6 em 6 meses até **36 meses**, que respeita o
limite. Se nem 6 meses respeita, a recomendação é não conceder.

**Nível de risco do relatório:** pelo risco em 12 meses, para ser comparável entre empresas:
baixo (até 5%), moderado (5–10%), alto (10–20%) e muito alto (acima de 20%).

**Regras antes do modelo:**

| Situação | Resultado |
|---|---|
| CNPJ inválido ou inexistente na base | O agent diz que não pode analisar |
| Empresa não ativa | Não conceder |
| Situação especial (ex.: recuperação judicial, falência) | Não conceder |
| MEI, administração pública, pessoa física com CNPJ, organismo internacional | Fora do escopo do modelo |

**Avisos de confiança:** empresa com menos de 3 anos, valores fora da faixa do treino e prazo
pedido acima de 36 meses (mostrado como extrapolação e recusado pela política).

**Configuração:** `RISK_LIMITE_RISCO` e `RISK_PRAZO_MAXIMO_MESES`. O limite é decisão do negócio e
pode mudar sem alterar o código.

## Por que esses valores

Simulação no conjunto de teste (954 mil empresas não vistas no treino), em
[notebooks/03_politica_de_risco.ipynb](../../notebooks/03_politica_de_risco.ipynb). O limite é
aplicado ao prazo de 24 meses, que tem desfecho observado. A taxa média de saída é de 12,9%.

| Limite | Aprovados | Saída real entre aprovados | Saída real entre recusados | Empresas que saíram e seriam barradas |
|---|---|---|---|---|
| 5% | 21% | 2,3% | 15,8% | 96% |
| **10%** | **48%** | **5,2%** | **20,1%** | **81%** |
| 15% | 69% | 7,4% | 25,5% | 61% |
| 20% | 81% | 8,8% | 30,7% | 45% |

- **10%** aprova cerca de metade dos empregadores para 24 meses e 81% para 12 meses. Entre os
  aprovados, a saída real fica 2,5 vezes abaixo da média, e 81% das empresas que fecharam teriam
  sido barradas. "1 chance em 10 de perder o desconto em folha durante o prazo" é fácil de
  explicar ao analista e ao comitê de crédito.
- **5%** corta demais a oferta comercial. **15–20%** deixa passar de 40% a 55% das empresas que
  fecham.
- **36 meses** é o horizonte em que o modelo foi validado com desfecho observado: a política
  nunca aprova com base em risco extrapolado.

Distribuição do prazo máximo resultante: 3,6% não conceder; entre 15% e 18% em cada faixa de 6 a
24 meses; 8,6% até 30 meses; 23,6% até 36 meses.

## Alternativas consideradas

- **Limite de risco anual** (ex.: até 5% ao ano): como o risco anual de cada empresa é quase
  constante, vira "aprova todos os prazos ou nenhum" e não produz o prazo máximo que o case pede.
- **Teto de 48 meses** (prática de mercado): aprovaria prazos com risco extrapolado, sem
  validação. Fica disponível por configuração.
- **Perda esperada ponderada pelo saldo devedor**: é economicamente melhor, porque perder o canal
  no mês 30 custa menos do que no mês 3, já que o saldo cai. Mas exige taxa, amortização e
  recuperação (portabilidade, FGTS), que não estão nos dados públicos. Fica como evolução.

## Consequências

- O veredito é sempre explicável: risco no prazo comparado com um limite fixo e conhecido.
- Empresas mais arriscadas recebem prazos menores em vez de uma recusa seca, o que preserva a
  oferta comercial.
- O limite foi escolhido por análise de dados, sem dados de rentabilidade do produto. Com taxa,
  custo de captação e recuperação reais, ele deve ser recalibrado pela perda esperada.
