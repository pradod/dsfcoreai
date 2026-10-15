# ADR-0002: Fontes de dados e desenho temporal

- **Status:** Aceito
- **Data:** 2026-10-15

## Contexto

O risco que importa no consignado privado é o empregador deixar de existir antes do fim do
crédito. A pergunta é de **tempo até o evento** (análise de sobrevivência), e o case sugere os dados
abertos de CNPJ da Receita Federal, permitindo complementar com outras fontes públicas.

Uma fonte só ajuda o modelo de sobrevivência se passar em quatro testes:

1. **Nível da empresa:** tem CNPJ. Uma média por setor é redundante com o CNAE, que o modelo já
   aprende de milhões de empresas.
2. **Sem vazamento:** tem data, para garantir que a informação existia *antes* do desfecho.
3. **Disponível na consulta:** existe para qualquer CNPJ que o analista informar.
4. **Cobertura no público-alvo:** empregadores com funcionários CLT, não MEI.

### O que a investigação dos dados mostrou

- O dados.gov.br aponta para compartilhamentos Nextcloud da Receita, acessíveis por WebDAV. O
  caminho antigo (`/dados/cnpj/dados_abertos_cnpj/`) não existe mais.
- Há **fotos mensais de 2023-05 a 2026-10**, cada uma com cerca de 7,8 GB compactados.
- As 10 partes de cada tabela **não são amostras aleatórias**: são faixas contíguas de CNPJ básico,
  atribuído em sequência ao longo do tempo. `Empresas1` vai de `00000000` a `04631960` (empresas
  antigas). `Empresas0` vai de `41273589` a `99017782`: são as mais recentes, 30 milhões de
  registros, 70% de natureza 2135 (Empresário Individual, quase todos MEI). Usar algumas partes como
  recorte seria escolher uma **coorte por época de abertura** e enviesaria o modelo.
- Os arquivos usam `;` como separador, codificação latin-1 e aspas. Há razões sociais com `;`
  dentro das aspas, então a leitura exige um parser CSV de verdade.
- Há bytes de controle soltos (NUL, `0x1A`, `0x7F`, `0x8F`) em endereços e nomes fantasia: poucas
  dezenas em ~130 milhões de linhas, mas suficientes para o leitor CSV recusar o arquivo. Eles são
  removidos na extração e contados no log. A leitura usa modo estrito, então uma linha
  malformada interrompe a conversão em vez de ser descartada em silêncio.

## Decisão

### Fontes usadas

| Fonte | Papel | Teste de tempo |
|---|---|---|
| **Receita CNPJ**: Empresas, Estabelecimentos, Simples, Sócios e tabelas de domínio | Núcleo: define o evento, a duração e a maior parte das features | Duas fotos (abaixo) |
| **Regimes Tributários (ECF)**: Lucro Real, Presumido, Arbitrado e Imunes/Isentas | Feature de porte e formalidade contábil | Cada linha tem o ano da escrituração (2016–2024); só se usa o ano anterior à foto de origem |
| **PGFN, Dívida Ativa da União**: FGTS, Previdenciário e Não Previdenciário | Sinal de dificuldade financeira. Dívida de FGTS indica empregador que não deposita o FGTS dos funcionários | Fotos trimestrais desde 2020 e `DATA_INSCRICAO` por inscrição |
| **IBGE, Demografia das Empresas** | **Validação externa**, não feature: comparar nossas curvas com as taxas oficiais de sobrevivência das empresas com assalariados | Não se aplica |

### Desenho temporal: duas fotos

- **Foto de origem, 2023-05** (a mais antiga disponível): todas as features vêm dela. A PGFN usada é
  a do trimestre anterior (2023 T1). Toda dívida presente nessa foto já existia antes da data de
  referência, mesmo quando a `DATA_INSCRICAO` é desconhecida (a origem usa 01/01/1000 como
  marcador). A data de inscrição serve só para medir há quanto tempo a dívida existe.
- **Foto de desfecho, 2026-10**: a situação cadastral observada cerca de 41 meses depois define o
  evento e o tempo até ele.

Assim nenhuma feature é medida depois do desfecho, que é o problema de usar uma foto única: porte,
capital e Simples podem mudar junto com o encerramento. A foto mais recente também alimenta o
serviço: na consulta, o agent usa os dados atuais do CNPJ.

### Recorte

Para o treino, uma **amostra aleatória do CNPJ básico por hash, sorteada entre todas as partes**,
o que é reproduzível e não depende da época de abertura. O lookup do serviço usa a base completa,
então qualquer CNPJ existente pode ser consultado. Os filtros de público-alvo (MEI, natureza
jurídica, matriz) serão definidos e justificados na EDA.

### Fontes avaliadas e não usadas no modelo de sobrevivência

- **Novo CAGED e RAIS desidentificada:** não têm CNPJ, então como feature seriam redundantes com o
  CNAE. Elas respondem outra pergunta, a permanência do funcionário no emprego (risco de vínculo).
  Ficam como possível segundo componente de risco, fora deste modelo.
- **SCR.data (Banco Central):** inadimplência agregada por segmento. Mede outro evento e é
  redundante com o CNAE.
- **CEIS/CNEP (Portal da Transparência):** tem CNPJ, mas os casos são raríssimos e concentrados em
  fornecedores do governo. Acrescentaria complexidade sem efeito mensurável.

## Consequências

- O download completo tem cerca de 18,5 GB: duas fotos da Receita, os regimes e dois trimestres da
  PGFN. O comando é idempotente e retoma downloads parciais (`python -m consig_risk.data.download`).
- O acompanhamento de cerca de 41 meses permite **validar** prazos de até 36 meses com desfecho
  observado. Para prazos maiores, a curva é extrapolada pelo modelo, e o relatório precisa dizer
  isso ao analista.
- Os dados da PGFN incluem devedores pessoa física, com CPF e nome. Eles são descartados na
  ingestão (LGPD): só pessoa jurídica é mantida.
