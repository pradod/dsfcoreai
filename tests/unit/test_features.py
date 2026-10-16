import datetime as dt
from pathlib import Path
from typing import Any

import duckdb
import pytest

from consig_risk.features.cohort import cohort_sql, reference_date
from consig_risk.features.company import SnapshotPaths, company_features_sql, ecf_year_available

ORIGIN = dt.date(2023, 5, 13)
OUTCOME = dt.date(2026, 10, 11)


def _write(con: duckdb.DuckDBPyConnection, path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0])
    values = ", ".join("(" + ", ".join(_literal(row[c], c) for c in columns) + ")" for row in rows)
    select = f"SELECT * FROM (VALUES {values}) t({', '.join(columns)})"
    con.sql(f"COPY ({select}) TO '{path}' (FORMAT parquet)")


def _literal(value: Any, column: str) -> str:
    if value is None:
        # Tipado como nos dados reais; um NULL solto vira INTEGER.
        return "NULL::DATE" if column.startswith("data_") else "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dt.date):
        return f"DATE '{value.isoformat()}'"
    if isinstance(value, int | float):
        return str(value)
    return f"'{value}'"


def _estab(
    cnpj: str, situacao: int, data_sit: dt.date, matriz: bool = True, **kw: Any
) -> dict[str, Any]:
    return {
        "cnpj_basico": cnpj,
        "cnpj_ordem": "0001" if matriz else "0002",
        "cnpj_dv": "00",
        "matriz": matriz,
        "situacao_cadastral": situacao,
        "data_situacao_cadastral": data_sit,
        "motivo_situacao_cadastral": kw.get("motivo", "00"),
        "data_inicio_atividade": kw.get("inicio", dt.date(2015, 1, 10)),
        "cnae_fiscal_principal": kw.get("cnae", "4711302"),
        "n_cnaes_secundarios": 0,
        "uf": kw.get("uf", "SP"),
        "situacao_especial": None,
    }


def _empresa(cnpj: str, natureza: str = "2062") -> dict[str, Any]:
    return {
        "cnpj_basico": cnpj,
        "razao_social": f"EMPRESA {cnpj}",
        "natureza_juridica": natureza,
        "porte": "01",
        "capital_social": 10000.0,
    }


def _simples(cnpj: str, simples: bool = True, mei: bool = False, **kw: Any) -> dict[str, Any]:
    return {
        "cnpj_basico": cnpj,
        "opcao_simples": simples,
        "opcao_mei": mei,
        "data_exclusao_simples": kw.get("excl_simples"),
        "data_exclusao_mei": kw.get("excl_mei"),
    }


@pytest.fixture
def snapshots(tmp_path: Path) -> tuple[SnapshotPaths, Path]:
    """Duas fotos pequenas com casos que exercitam cada regra de tempo e de elegibilidade."""
    con = duckdb.connect()
    origin_dir, outcome_dir = tmp_path / "receita/2023-05", tmp_path / "receita/2026-10"

    _write(
        con,
        origin_dir / "Estabelecimentos/part0.parquet",
        [
            _estab("A0000001", 2, dt.date(2015, 1, 10)),  # ativa, sobrevive
            _estab("A0000001", 2, dt.date(2018, 1, 1), matriz=False, uf="RJ"),  # filial ativa
            _estab("A0000002", 2, ORIGIN, inicio=dt.date(2021, 5, 13)),  # ativa, baixa depois
            _estab("A0000003", 2, dt.date(2010, 1, 1)),  # ativa, MEI -> fora
            _estab("A0000004", 2, dt.date(2010, 1, 1)),  # órgão público -> fora
            _estab("A0000005", 8, dt.date(2020, 1, 1)),  # já baixada na origem -> fora
            _estab("A0000006", 2, dt.date(2012, 1, 1)),  # ativa, baixa retroativa
            # Caso real (08314885): duas "matrizes" ativas, uma delas repetida na base.
            {**_estab("A0000007", 2, dt.date(2020, 8, 25)), "cnpj_ordem": "0039"},
            {**_estab("A0000007", 2, dt.date(2020, 8, 25)), "cnpj_ordem": "0039"},
            {**_estab("A0000007", 2, dt.date(2022, 1, 29)), "cnpj_ordem": "0047"},
        ],
    )
    _write(
        con,
        origin_dir / "Empresas/part0.parquet",
        [
            _empresa("A0000001"),
            _empresa("A0000002"),
            _empresa("A0000003", "2135"),
            _empresa("A0000004", "1031"),
            _empresa("A0000005"),
            _empresa("A0000006"),
            _empresa("A0000007"),
        ],
    )
    _write(
        con,
        origin_dir / "Simples/part0.parquet",
        [
            _simples("A0000001"),
            # Excluído do Simples 3 meses antes da origem: sinal legítimo.
            _simples("A0000002", simples=False, excl_simples=dt.date(2023, 2, 1)),
            _simples("A0000003", mei=True),
        ],
    )
    _write(
        con,
        origin_dir / "Socios/part0.parquet",
        [
            {
                "cnpj_basico": "A0000001",
                "identificador_socio": 1,
                "qualificacao_socio": "22",
                "data_entrada_sociedade": dt.date(2023, 1, 1),
                "socio_estrangeiro": False,
                "faixa_etaria": 0,
            },
            {  # entrada depois da origem: não pode aparecer na foto de origem
                "cnpj_basico": "A0000001",
                "identificador_socio": 2,
                "qualificacao_socio": "49",
                "data_entrada_sociedade": dt.date(2024, 1, 1),
                "socio_estrangeiro": True,
                "faixa_etaria": 4,
            },
        ],
    )
    pgfn_dir = tmp_path / "pgfn/2023_trimestre_01"
    _write(
        con,
        pgfn_dir / "fgts.parquet",
        [
            {
                "cnpj_basico": "A0000002",
                "tipo_devedor": "PRINCIPAL",
                "origem": "fgts",
                "valor_consolidado": 1500.0,
                "ajuizado": False,
                "data_inscricao": dt.date(2022, 11, 1),
            },
            {  # corresponsável: não é dívida da própria empresa
                "cnpj_basico": "A0000001",
                "tipo_devedor": "CORRESPONSAVEL",
                "origem": "fgts",
                "valor_consolidado": 99.0,
                "ajuizado": False,
                "data_inscricao": dt.date(2022, 1, 1),
            },
        ],
    )
    regimes = tmp_path / "regimes/regimes.parquet"
    _write(
        con,
        regimes,
        [
            {"cnpj_basico": "A0000006", "ano": 2021, "forma_de_tributacao": "LUCRO REAL"},
            # ECF de 2022 só é entregue em jul/2023, depois da origem: não pode ser usada.
            {"cnpj_basico": "A0000001", "ano": 2022, "forma_de_tributacao": "LUCRO PRESUMIDO"},
        ],
    )

    _write(
        con,
        outcome_dir / "Estabelecimentos/part0.parquet",
        [
            _estab("A0000001", 2, dt.date(2015, 1, 10)),
            _estab("A0000002", 8, dt.date(2024, 5, 13), motivo="01"),
            _estab("A0000003", 8, dt.date(2024, 1, 1)),
            _estab("A0000004", 2, dt.date(2010, 1, 1)),
            _estab("A0000005", 8, dt.date(2020, 1, 1)),
            _estab("A0000006", 4, dt.date(2023, 1, 1), motivo="63"),
        ],
    )
    origin = SnapshotPaths(
        receita_dir=origin_dir,
        pgfn_dir=pgfn_dir,
        regimes_path=regimes,
        ref_date=ORIGIN,
        ecf_year=ecf_year_available(ORIGIN),
    )
    return origin, outcome_dir


def _rows(sql: str) -> dict[str, dict[str, Any]]:
    rel = duckdb.sql(sql)
    return {r[0]: dict(zip(rel.columns, r, strict=True)) for r in rel.fetchall()}


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        (dt.date(2023, 5, 13), 2021),  # ECF 2022 ainda não entregue
        (dt.date(2023, 7, 31), 2021),
        (dt.date(2023, 8, 1), 2022),
        (dt.date(2026, 10, 11), 2025),
    ],
)
def test_ecf_year_available_respects_july_deadline(ref: dt.date, expected: int) -> None:
    assert ecf_year_available(ref) == expected


def test_company_features_use_only_information_available_at_reference_date(
    snapshots: tuple[SnapshotPaths, Path],
) -> None:
    origin, _ = snapshots

    rows = _rows(company_features_sql(origin))

    a1, a2, a6 = rows["A0000001"], rows["A0000002"], rows["A0000006"]
    assert a1["n_socios"] == 1  # sócio que entrou em 2024 não existe na origem
    assert a1["tem_socio_estrangeiro"] is False
    assert a1["n_filiais_ativas"] == 1 and a1["n_ufs"] == 2
    assert a1["regime_tributario"] == "simples"  # ECF 2022 ignorada
    assert a1["divida_fgts"] is False  # dívida como corresponsável não conta
    assert a2["divida_fgts"] is True and a2["meses_desde_ultima_inscricao"] == 6
    assert a2["valor_divida"] == 1500.0
    assert a2["excluido_simples_12m"] is True
    assert a2["idade_meses"] == 24
    assert a2["regime_tributario"] == "sem_ecf"
    assert a6["regime_tributario"] == "real"
    assert a1["cnae_secao"] == "G"


def test_cohort_keeps_only_eligible_companies_and_measures_time_to_event(
    snapshots: tuple[SnapshotPaths, Path],
) -> None:
    origin, outcome_dir = snapshots

    rows = _rows(cohort_sql(origin, outcome_dir, OUTCOME))

    # MEI, órgão público e empresa já baixada na origem ficam de fora.
    assert set(rows) == {"A0000001", "A0000002", "A0000006"}
    survivor, closed, retro = rows["A0000001"], rows["A0000002"], rows["A0000006"]
    assert survivor["evento"] is False
    assert survivor["duracao_meses"] == pytest.approx(41, abs=0.1)
    assert closed["evento"] is True and closed["tipo_evento"] == "baixada"
    assert closed["duracao_meses"] == pytest.approx(12, abs=0.1)
    assert retro["tipo_evento"] == "inapta"
    assert 0 < retro["duracao_meses"] < 0.1  # data retroativa vira evento no início
    assert all(0 <= r["amostra"] < 1000 for r in rows.values())


def test_reference_date_is_latest_registry_event(snapshots: tuple[SnapshotPaths, Path]) -> None:
    origin, _ = snapshots

    assert reference_date(duckdb.connect(), origin.receita_dir) == ORIGIN


def test_company_features_keep_one_row_per_company_when_registry_has_duplicate_matriz(
    snapshots: tuple[SnapshotPaths, Path],
) -> None:
    origin, _ = snapshots

    rel = duckdb.sql(company_features_sql(origin))
    rows = rel.fetchall()

    ids = [r[0] for r in rows]
    assert len(ids) == len(set(ids))
    dup = dict(zip(rel.columns, next(r for r in rows if r[0] == "A0000007"), strict=True))
    assert dup["cnpj_ordem"] == "0047"  # entre ativas, a de evento cadastral mais recente
