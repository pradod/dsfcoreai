import datetime as dt
import io
import zipfile
from pathlib import Path

import duckdb

from consig_risk.data.build import convert_zip, extract_clean
from consig_risk.data.layout import EMPRESAS, ESTABELECIMENTOS

# Linhas no formato real da Receita: latin-1, ";" como separador, tudo entre aspas.
EMPRESAS_CSV = (
    '"00000000";"BANCO DO BRASIL SA";"2038";"10";"120000000000,00";"05";""\n'
    '"00000001";"PADARIA; CONFEITARIA AÇÚCAR LTDA";"2062";"49";"0,00";"01";""\n'
).encode("latin-1")

ESTABELECIMENTOS_CSV = (
    '"00000000";"0001";"91";"1";"DIRECAO GERAL";"02";"20051103";"00";"";"";"19660801";'
    '"6422100";"6499999,6619399";"QUADRA";"SAUN";"5";"";"ASA NORTE";"70040912";"DF";"9701";'
    '"61";"34939002";"";"";"";"";"";"";""\n'
    '"00000002";"0001";"10";"2";"";"08";"0";"01";"";"";"20100101";"4120400";"";"RUA";"X";'
    '"1";"";"";"";"SP";"7107";"";"";"";"";"";"";"";"RECUPERACAO JUDICIAL";"20200315"\n'
).encode("latin-1")


def _zip(tmp_path: Path, name: str, payload: bytes) -> Path:
    path = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{name}.CSV", payload)
    return path


def test_convert_empresas_handles_latin1_quoted_separator_and_decimal_comma(
    tmp_path: Path,
) -> None:
    dest = convert_zip(
        _zip(tmp_path, "Empresas1", EMPRESAS_CSV), EMPRESAS, tmp_path / "e.parquet", tmp_path
    )

    rows = duckdb.sql(f"SELECT * FROM '{dest}' ORDER BY cnpj_basico").fetchall()

    assert rows[0] == (
        "00000000",
        "BANCO DO BRASIL SA",
        "2038",
        "10",
        120_000_000_000.0,
        "05",
        None,
    )
    assert rows[1][1] == "PADARIA; CONFEITARIA AÇÚCAR LTDA"
    assert rows[1][4] == 0.0


def test_convert_estabelecimentos_types_dates_flags_and_drops_contact_data(tmp_path: Path) -> None:
    dest = convert_zip(
        _zip(tmp_path, "Estabelecimentos1", ESTABELECIMENTOS_CSV),
        ESTABELECIMENTOS,
        tmp_path / "est.parquet",
        tmp_path,
    )

    rel = duckdb.sql(f"SELECT * FROM '{dest}' ORDER BY cnpj_basico")
    rows = [dict(zip(rel.columns, r, strict=True)) for r in rel.fetchall()]

    bb, closed = rows
    assert bb["matriz"] is True
    assert bb["situacao_cadastral"] == 2
    assert bb["data_inicio_atividade"] == dt.date(1966, 8, 1)
    assert bb["n_cnaes_secundarios"] == 2
    assert closed["matriz"] is False
    assert closed["data_situacao_cadastral"] is None  # "0" na origem
    assert closed["situacao_especial"] == "RECUPERACAO JUDICIAL"
    assert closed["data_situacao_especial"] == dt.date(2020, 3, 15)
    assert not {"telefone_1", "correio_eletronico", "logradouro"} & set(rel.columns)


def test_convert_removes_control_bytes_found_in_real_receita_files(tmp_path: Path) -> None:
    # Estabelecimentos0.zip de 2023-05 tem NUL, 0x1A, 0x7F e 0x8F soltos em endereços e nomes.
    corrupted = EMPRESAS_CSV.replace(b"BANCO DO BRASIL", b"BANCO\x7f DO\x00 BRA\x8fSIL\x1a")

    dest = convert_zip(
        _zip(tmp_path, "Empresas1", corrupted), EMPRESAS, tmp_path / "e.parquet", tmp_path
    )

    names = [r[0] for r in duckdb.sql(f"SELECT razao_social FROM '{dest}'").fetchall()]
    assert "BANCO DO BRASIL SA" in names


def test_extract_clean_keeps_text_and_line_breaks_and_counts_removed_bytes() -> None:
    src, dst = io.BytesIO("a\x00b\x1a\tç\r\n\x8fc".encode("latin-1")), io.BytesIO()

    assert extract_clean(src, dst) == 3
    assert dst.getvalue() == "ab\tç\r\nc".encode("latin-1")


def test_convert_is_idempotent(tmp_path: Path) -> None:
    zip_path = _zip(tmp_path, "Empresas1", EMPRESAS_CSV)
    dest = convert_zip(zip_path, EMPRESAS, tmp_path / "e.parquet", tmp_path)
    mtime = dest.stat().st_mtime_ns

    convert_zip(zip_path, EMPRESAS, dest, tmp_path)

    assert dest.stat().st_mtime_ns == mtime
