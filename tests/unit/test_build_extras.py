import datetime as dt
import zipfile
from pathlib import Path

import duckdb

from consig_risk.data.build_extras import build_pgfn_quarter, build_regimes

PGFN_HEADER = (
    "CPF_CNPJ;TIPO_PESSOA;TIPO_DEVEDOR;NOME_DEVEDOR;UF_DEVEDOR;UNIDADE_RESPONSAVEL;"
    "NUMERO_INSCRICAO;TIPO_SITUACAO_INSCRICAO;SITUACAO_INSCRICAO;{natureza};DATA_INSCRICAO;"
    "INDICADOR_AJUIZADO;VALOR_CONSOLIDADO\n"
)


def _write_zip(path: Path, members: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)


def _pgfn_csv(natureza_col: str, rows: list[str]) -> bytes:
    return (PGFN_HEADER.format(natureza=natureza_col) + "\n".join(rows) + "\n").encode("latin-1")


def test_build_regimes_keeps_own_company_rows_and_normalizes_cnpj(tmp_path: Path) -> None:
    raw, out = tmp_path / "raw", tmp_path / "out"
    header = "ano,cnpj,cnpj_da_scp,forma_de_tributacao,quantidade_de_escrituracoes\n"
    real = (
        header + "2022,00.000.000/0001-91,0,LUCRO REAL,1\n2022,11.111.111/0001-11,22,LUCRO REAL,1\n"
    )
    presumido = header + "2021,33.333.333/0001-33,0,LUCRO PRESUMIDO,1\n"
    _write_zip(raw / "regimes/entidades-lucro-real.zip", {"Lucro Real.csv": real.encode()})
    _write_zip(
        raw / "regimes/entidades-lucro-presumido.zip",
        {"Lucro Presumido 2021.csv": presumido.encode()},
    )
    for name in ("entidades-lucro-arbitrado.zip", "entidades-imunes-e-isentas.zip"):
        _write_zip(raw / "regimes" / name, {"vazio.csv": header.encode()})
    out.mkdir()

    dest = build_regimes(raw, out)

    rows = duckdb.sql(f"SELECT * FROM '{dest}' ORDER BY cnpj_basico").fetchall()
    assert rows == [("00000000", 2022, "LUCRO REAL"), ("33333333", 2021, "LUCRO PRESUMIDO")]


def test_build_pgfn_keeps_only_companies_drops_names_and_unknown_dates(tmp_path: Path) -> None:
    raw, out = tmp_path / "raw", tmp_path / "out"
    quarter_dir = raw / "pgfn/2023_trimestre_01"
    _write_zip(
        quarter_dir / "Dados_abertos_FGTS.zip",
        {
            "fgts_1.csv": _pgfn_csv(
                "RECEITA_PRINCIPAL",
                [
                    "10.496.760/0001-95;Pessoa jurídica;Principal;EMPRESA X;BA;ACRE;FG1;"
                    "Em cobrança;INSCRITA;Contribuições FGTS;30/07/2019;NAO;4461.14",
                    "XXX735.623XX;Pessoa física;PRINCIPAL;JOSE DA SILVA;SP;SP;FG2;"
                    "Em cobrança;INSCRITA;Contribuições FGTS;01/02/2020;SIM;100.00",
                ],
            )
        },
    )
    _write_zip(
        quarter_dir / "Dados_abertos_Previdenciario.zip",
        {
            "prev_1.csv": _pgfn_csv(
                "TIPO_CREDITO",
                [
                    "21.565.684/0000-14;Pessoa jurídica;Principal;EMPRESA Y;DF;DF;PV1;"
                    "Em cobrança;ATIVA;Contribuição patronal;01/01/1000;SIM;.00",
                ],
            )
        },
    )
    _write_zip(
        quarter_dir / "Dados_abertos_Nao_Previdenciario.zip",
        {"sida_1.csv": _pgfn_csv("RECEITA_PRINCIPAL", [])},
    )
    out.mkdir()

    outputs = build_pgfn_quarter(raw, out, "2023_trimestre_01")
    rel = duckdb.sql(
        "SELECT * FROM read_parquet([{}], union_by_name=true) ORDER BY cnpj".format(
            ", ".join(f"'{p}'" for p in outputs)
        )
    )
    rows = [dict(zip(rel.columns, r, strict=True)) for r in rel.fetchall()]

    assert [r["cnpj"] for r in rows] == ["10496760000195", "21565684000014"]
    fgts, prev = rows
    assert fgts["cnpj_basico"] == "10496760"
    assert fgts["data_inscricao"] == dt.date(2019, 7, 30)
    assert fgts["valor_consolidado"] == 4461.14
    assert fgts["ajuizado"] is False
    assert fgts["origem"] == "fgts"
    assert prev["natureza_debito"] == "Contribuição patronal"
    assert prev["data_inscricao"] is None  # 01/01/1000 é marcador de data desconhecida
    assert prev["valor_consolidado"] == 0.0
    assert "nome_devedor" not in {c.lower() for c in rel.columns}
