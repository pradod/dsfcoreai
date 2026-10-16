"""Features de uma empresa (CNPJ básico) "como eram" numa data de referência.

O mesmo SQL gera as features do treino (foto de origem, 2023-05) e as do serviço (foto mais
recente). Toda informação usada precisa existir até a data de referência: é isso que impede o
modelo de aprender com consequências do próprio desfecho (ver ADR-0002 e a EDA).
"""

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import duckdb

from consig_risk.data import sources

# Seções da CNAE 2.0 por faixa de divisão (dois primeiros dígitos), conforme o IBGE.
CNAE_SECTIONS: tuple[tuple[int, int, str], ...] = (
    (1, 3, "A"),
    (5, 9, "B"),
    (10, 33, "C"),
    (35, 35, "D"),
    (36, 39, "E"),
    (41, 43, "F"),
    (45, 47, "G"),
    (49, 53, "H"),
    (55, 56, "I"),
    (58, 63, "J"),
    (64, 66, "K"),
    (68, 68, "L"),
    (69, 75, "M"),
    (77, 82, "N"),
    (84, 84, "O"),
    (85, 85, "P"),
    (86, 88, "Q"),
    (90, 93, "R"),
    (94, 96, "S"),
    (97, 97, "T"),
    (99, 99, "U"),
)

# Situação cadastral (dicionário da Receita).
SITUACAO_ATIVA = 2


def ecf_year_available(ref_date: dt.date) -> int:
    """Último ano-calendário de ECF já entregue na data de referência.

    A ECF do ano N vence no último dia útil de julho de N+1. Em 13/05/2023 a ECF de 2022 ainda não
    tinha sido entregue, então o último ano disponível é 2021.
    """
    return ref_date.year - 1 if ref_date.month > 7 else ref_date.year - 2


@dataclass(frozen=True)
class SnapshotPaths:
    """Onde estão os Parquets de uma foto e qual a data de referência dela."""

    receita_dir: Path
    pgfn_dir: Path
    regimes_path: Path
    ref_date: dt.date
    ecf_year: int

    @classmethod
    def for_snapshot(
        cls, interim_dir: Path, snapshot: sources.Snapshot, ref_date: dt.date
    ) -> "SnapshotPaths":
        return cls(
            receita_dir=interim_dir / "receita" / snapshot.receita_month,
            pgfn_dir=interim_dir / "pgfn" / snapshot.pgfn_quarter,
            regimes_path=interim_dir / "regimes" / "regimes.parquet",
            ref_date=ref_date,
            ecf_year=ecf_year_available(ref_date),
        )


def _cnae_section_sql(col: str) -> str:
    division = f"try_cast(left({col}, 2) AS INTEGER)"
    cases = " ".join(
        f"WHEN {division} BETWEEN {lo} AND {hi} THEN '{sec}'" for lo, hi, sec in CNAE_SECTIONS
    )
    return f"CASE {cases} END"


def company_features_sql(paths: SnapshotPaths) -> str:
    """SQL que devolve uma linha por empresa (matriz) com as features na data de referência.

    Inclui empresas em qualquer situação cadastral; quem filtra o público é o chamador.
    """
    ref = f"DATE '{paths.ref_date.isoformat()}'"
    r = paths.receita_dir
    return f"""
    WITH
    est AS (SELECT * FROM read_parquet('{r}/Estabelecimentos/*.parquet')),
    matriz AS (SELECT * FROM est WHERE matriz),
    filiais AS (
      SELECT cnpj_basico,
             count(*) FILTER (WHERE NOT matriz AND situacao_cadastral = {SITUACAO_ATIVA})
               AS n_filiais_ativas,
             count(DISTINCT uf) FILTER (WHERE situacao_cadastral = {SITUACAO_ATIVA}) AS n_ufs
      FROM est GROUP BY cnpj_basico
    ),
    socios AS (
      SELECT cnpj_basico,
             count(*) AS n_socios,
             bool_or(identificador_socio = 1) AS tem_socio_pj,
             bool_or(socio_estrangeiro) AS tem_socio_estrangeiro,
             count(*) FILTER (WHERE data_entrada_sociedade > {ref} - INTERVAL 12 MONTH)
               AS n_socios_entrada_12m
      FROM read_parquet('{r}/Socios/*.parquet')
      WHERE data_entrada_sociedade IS NULL OR data_entrada_sociedade <= {ref}
      GROUP BY cnpj_basico
    ),
    divida AS (
      SELECT cnpj_basico,
             bool_or(origem = 'fgts') AS divida_fgts,
             bool_or(origem = 'previdenciario') AS divida_previdenciaria,
             bool_or(origem = 'nao_previdenciario') AS divida_nao_previdenciaria,
             count(*) AS n_inscricoes,
             sum(valor_consolidado) AS valor_divida,
             bool_or(ajuizado) AS divida_ajuizada,
             max(data_inscricao) AS ultima_inscricao
      FROM read_parquet('{paths.pgfn_dir}/*.parquet')
      WHERE tipo_devedor = 'PRINCIPAL'
      GROUP BY cnpj_basico
    ),
    ecf AS (
      SELECT cnpj_basico,
             CASE
               WHEN bool_or(forma_de_tributacao LIKE '%REAL%') THEN 'real'
               WHEN bool_or(forma_de_tributacao LIKE '%PRESUMIDO%') THEN 'presumido'
               WHEN bool_or(forma_de_tributacao LIKE '%ARBITRADO%') THEN 'arbitrado'
               ELSE 'imune_isenta'
             END AS regime_ecf
      FROM read_parquet('{paths.regimes_path}')
      WHERE ano = {paths.ecf_year}
      GROUP BY cnpj_basico
    )
    SELECT
      m.cnpj_basico,
      m.cnpj_ordem,
      m.cnpj_dv,
      e.razao_social,
      m.situacao_cadastral,
      m.data_situacao_cadastral,
      m.motivo_situacao_cadastral,
      m.data_inicio_atividade,
      date_diff('month', m.data_inicio_atividade, {ref}) AS idade_meses,
      e.natureza_juridica,
      left(e.natureza_juridica, 1) AS grupo_natureza,
      coalesce(e.porte, '00') AS porte,
      e.capital_social,
      m.cnae_fiscal_principal,
      left(m.cnae_fiscal_principal, 2) AS cnae_divisao,
      {_cnae_section_sql("m.cnae_fiscal_principal")} AS cnae_secao,
      m.n_cnaes_secundarios,
      m.uf,
      m.situacao_especial IS NOT NULL AS tem_situacao_especial,
      coalesce(f.n_filiais_ativas, 0) AS n_filiais_ativas,
      coalesce(f.n_ufs, 0) AS n_ufs,
      coalesce(s.opcao_simples, false) AS opcao_simples,
      coalesce(s.opcao_mei, false) AS opcao_mei,
      coalesce(s.data_exclusao_simples > {ref} - INTERVAL 12 MONTH
               AND s.data_exclusao_simples <= {ref}, false) AS excluido_simples_12m,
      coalesce(s.data_exclusao_mei <= {ref}, false) AS ex_mei,
      coalesce(so.n_socios, 0) AS n_socios,
      coalesce(so.tem_socio_pj, false) AS tem_socio_pj,
      coalesce(so.tem_socio_estrangeiro, false) AS tem_socio_estrangeiro,
      coalesce(so.n_socios_entrada_12m, 0) AS n_socios_entrada_12m,
      coalesce(d.divida_fgts, false) AS divida_fgts,
      coalesce(d.divida_previdenciaria, false) AS divida_previdenciaria,
      coalesce(d.divida_nao_previdenciaria, false) AS divida_nao_previdenciaria,
      coalesce(d.n_inscricoes, 0) AS n_inscricoes_divida,
      coalesce(d.valor_divida, 0) AS valor_divida,
      coalesce(d.divida_ajuizada, false) AS divida_ajuizada,
      date_diff('month', d.ultima_inscricao, {ref}) AS meses_desde_ultima_inscricao,
      CASE
        WHEN ecf.regime_ecf IS NOT NULL THEN ecf.regime_ecf
        WHEN coalesce(s.opcao_simples, false) THEN 'simples'
        ELSE 'sem_ecf'
      END AS regime_tributario
    FROM matriz m
    LEFT JOIN read_parquet('{r}/Empresas/*.parquet') e USING (cnpj_basico)
    LEFT JOIN read_parquet('{r}/Simples/*.parquet') s USING (cnpj_basico)
    LEFT JOIN filiais f USING (cnpj_basico)
    LEFT JOIN socios so USING (cnpj_basico)
    LEFT JOIN divida d USING (cnpj_basico)
    LEFT JOIN ecf USING (cnpj_basico)
    """


def company_features(
    con: duckdb.DuckDBPyConnection, paths: SnapshotPaths
) -> duckdb.DuckDBPyRelation:
    return con.sql(company_features_sql(paths))
