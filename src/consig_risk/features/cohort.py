"""Coorte de treino: empresas elegíveis na foto de origem e o que aconteceu com elas depois.

Cada linha é uma empresa ativa na origem, com as features daquela data, o tempo observado até o
evento (ou até a foto de desfecho, se continuou ativa) e o tipo de evento. É um dado de
sobrevivência com censura à direita: quem continua ativa só nos diz que durou *pelo menos* aquele
tempo.
"""

import datetime as dt
from pathlib import Path

import duckdb

from consig_risk.features.company import SITUACAO_ATIVA, SnapshotPaths, company_features_sql

DAYS_PER_MONTH = 365.25 / 12

# Público-alvo (decidido na EDA, ver notebooks/01_eda.ipynb):
# - grupo 2 (entidades empresariais) e 3 (sem fins lucrativos) contratam CLT;
# - grupo 1 (administração pública), 4 (pessoas físicas com CNPJ, ex.: candidatos a cargo
#   político) e 5 (organismos internacionais) ficam fora do consignado privado;
# - MEI fica fora: quase nunca tem empregado e tem dinâmica de saída própria.
GRUPOS_NATUREZA_ELEGIVEIS = ("2", "3")

SAMPLE_BUCKETS = 1000


def reference_date(con: duckdb.DuckDBPyConnection, receita_dir: Path) -> dt.date:
    """Data de referência de uma foto: o evento cadastral mais recente registrado nela."""
    row = con.sql(
        f"SELECT max(data_situacao_cadastral) "
        f"FROM read_parquet('{receita_dir}/Estabelecimentos/*.parquet')"
    ).fetchone()
    if row is None or row[0] is None:
        raise ValueError(f"foto sem datas de situação cadastral: {receita_dir}")
    value: dt.date = row[0]
    return value


def eligibility_sql(alias: str = "f") -> str:
    grupos = ", ".join(f"'{g}'" for g in GRUPOS_NATUREZA_ELEGIVEIS)
    return (
        f"{alias}.situacao_cadastral = {SITUACAO_ATIVA} "
        f"AND NOT {alias}.opcao_mei "
        f"AND {alias}.grupo_natureza IN ({grupos})"
    )


def cohort_sql(origin: SnapshotPaths, outcome_dir: Path, outcome_date: dt.date) -> str:
    start = f"DATE '{origin.ref_date.isoformat()}'"
    end = f"DATE '{outcome_date.isoformat()}'"
    return f"""
    WITH
    f AS ({company_features_sql(origin)}),
    desfecho AS (
      SELECT cnpj_basico,
             situacao_cadastral AS situacao_final,
             data_situacao_cadastral AS data_situacao_final,
             motivo_situacao_cadastral AS motivo_final
      FROM read_parquet('{outcome_dir}/Estabelecimentos/*.parquet')
      WHERE matriz
    )
    SELECT
      f.*,
      d.situacao_final <> {SITUACAO_ATIVA} AS evento,
      CASE d.situacao_final
        WHEN 8 THEN 'baixada' WHEN 4 THEN 'inapta' WHEN 3 THEN 'suspensa' WHEN 1 THEN 'nula'
      END AS tipo_evento,
      d.motivo_final AS motivo_evento,
      -- Alguns eventos são registrados com data retroativa (anterior à origem): contam como
      -- evento logo no início do acompanhamento.
      CASE
        WHEN d.situacao_final <> {SITUACAO_ATIVA}
          THEN greatest(date_diff('day', {start}, d.data_situacao_final), 1)
        ELSE date_diff('day', {start}, {end})
      END / {DAYS_PER_MONTH} AS duracao_meses,
      CAST(md5_number(f.cnpj_basico) % {SAMPLE_BUCKETS} AS SMALLINT) AS amostra
    FROM f
    JOIN desfecho d USING (cnpj_basico)
    WHERE {eligibility_sql("f")}
    """
