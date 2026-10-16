"""Converte os zips brutos da Receita em Parquet tipado, uma parte por vez.

Uso:
    python -m consig_risk.data.build                      # todas as fotos baixadas
    python -m consig_risk.data.build --months 2026-10 --tables Empresas

Saída: data/interim/receita/<AAAA-MM>/<Tabela>/part<N>.parquet. É idempotente: partes já
convertidas são puladas. Cada parte é extraída para um CSV temporário (o DuckDB não lê .zip),
convertida e o temporário é apagado, então o disco extra necessário é o de uma parte só.
"""

import argparse
import logging
import tempfile
import time
import zipfile
from pathlib import Path
from typing import IO

from consig_risk import duck
from consig_risk.config import get_settings
from consig_risk.data import sources
from consig_risk.data.layout import TABLES, TableSpec

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 16 << 20

# Bytes de controle (exceto tab, LF e CR) e a faixa C1 do latin-1: não têm significado em texto e
# o leitor CSV do DuckDB os rejeita. Aparecem soltos em campos de endereço e nome fantasia.
_CONTROL_BYTES = bytes([b for b in range(0x20) if b not in (0x09, 0x0A, 0x0D)]) + bytes(
    range(0x7F, 0xA0)
)


def extract_clean(src: IO[bytes], dst: IO[bytes]) -> int:
    """Copia `src` para `dst` removendo bytes de controle. Retorna quantos foram removidos."""
    removed = 0
    while chunk := src.read(_CHUNK_SIZE):
        cleaned = chunk.translate(None, _CONTROL_BYTES)
        removed += len(chunk) - len(cleaned)
        dst.write(cleaned)
    return removed


def convert_zip(zip_path: Path, spec: TableSpec, dest: Path, tmp_dir: Path) -> Path:
    """Converte um zip da Receita (um CSV dentro) em um Parquet com as colunas de `spec`."""
    if dest.exists():
        logger.info("já convertido: %s", dest)
        return dest

    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        if len(members) != 1:
            raise ValueError(
                f"{zip_path.name}: esperado 1 arquivo no zip, encontrados {len(members)}"
            )
        csv_path = tmp_dir / f"{zip_path.stem}.csv"
        with archive.open(members[0]) as src, csv_path.open("wb") as dst:
            removed = extract_clean(src, dst)
    if removed:
        logger.warning("%s: %d bytes de controle removidos", zip_path.name, removed)

    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".tmp")
    partial.unlink(missing_ok=True)
    try:
        columns = ", ".join(f"'{c}': 'VARCHAR'" for c in spec.raw_columns)
        duck.connect().execute(
            f"""
            COPY (
              SELECT
                {spec.select_sql()}
              FROM read_csv(
                '{csv_path}',
                delim=';', quote='"', escape='"', header=false,
                encoding='latin-1', columns={{{columns}}}
              )
            ) TO '{partial}' (FORMAT parquet, COMPRESSION zstd)
            """
        )
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    finally:
        csv_path.unlink(missing_ok=True)
    partial.replace(dest)
    return dest


def build_snapshot(
    raw_dir: Path, out_dir: Path, month: str, tables: tuple[str, ...] | None
) -> None:
    month_raw = raw_dir / "receita" / month
    for zip_name in sources.receita_files(tables):
        zip_path = month_raw / zip_name
        if not zip_path.exists():
            raise FileNotFoundError(
                f"{zip_path} não existe; rode `python -m consig_risk.data.download`"
            )
        table = zip_name.removesuffix(".zip").rstrip("0123456789")
        part = zip_name.removesuffix(".zip").removeprefix(table) or "0"
        dest = out_dir / "receita" / month / table / f"part{part}.parquet"
        started = time.monotonic()
        with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
            convert_zip(zip_path, TABLES[table], dest, Path(tmp))
        logger.info("%s/%s em %.1fs", month, dest.relative_to(out_dir), time.monotonic() - started)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--months",
        nargs="+",
        default=[sources.ORIGIN.receita_month, sources.LATEST.receita_month],
    )
    parser.add_argument("--tables", nargs="+", help="tabelas da Receita (padrão: todas)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    out_dir = settings.data_dir / "interim"
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    for month in args.months:
        build_snapshot(
            settings.data_dir / "raw", out_dir, month, tuple(args.tables) if args.tables else None
        )
    logger.info("concluído em %.1f min", (time.monotonic() - started) / 60)


if __name__ == "__main__":
    main()
