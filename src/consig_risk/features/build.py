"""Gera os datasets processados a partir dos Parquets intermediários.

Uso:
    python -m consig_risk.features.build

Saídas:
    data/processed/cohort.parquet     coorte de treino (features na origem + desfecho)
    data/processed/companies.parquet  features de todas as empresas na foto mais recente,
                                      ordenadas por CNPJ básico (lookup rápido no serviço)
    data/processed/companies.json     data de referência e fontes dessa foto
"""

import json
import logging
import time
from dataclasses import replace
from pathlib import Path

import duckdb

from consig_risk.config import get_settings
from consig_risk.data import sources
from consig_risk.features.cohort import cohort_sql, reference_date
from consig_risk.features.company import SnapshotPaths, company_features_sql

logger = logging.getLogger(__name__)


def _copy(con: duckdb.DuckDBPyConnection, query: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".tmp")
    partial.unlink(missing_ok=True)
    started = time.monotonic()
    con.sql(f"COPY ({query}) TO '{partial}' (FORMAT parquet, COMPRESSION zstd)")
    partial.replace(dest)
    logger.info("%s em %.1fs", dest, time.monotonic() - started)


def build(interim_dir: Path, processed_dir: Path, force: bool = False) -> None:
    con = duckdb.connect()
    origin_dir = interim_dir / "receita" / sources.ORIGIN.receita_month
    latest_dir = interim_dir / "receita" / sources.LATEST.receita_month
    origin = SnapshotPaths.for_snapshot(
        interim_dir, sources.ORIGIN, reference_date(con, origin_dir)
    )
    latest = SnapshotPaths.for_snapshot(
        interim_dir, sources.LATEST, reference_date(con, latest_dir)
    )
    # A ECF do último ano "entregue" pela regra pode ainda não estar publicada: usa o mais recente.
    published = con.sql(f"SELECT max(ano) FROM read_parquet('{latest.regimes_path}')").fetchone()
    if published and published[0] is not None and published[0] < latest.ecf_year:
        latest = replace(latest, ecf_year=int(published[0]))
    logger.info("origem: %s (ECF %s)", origin.ref_date, origin.ecf_year)
    logger.info("desfecho/serviço: %s (ECF %s)", latest.ref_date, latest.ecf_year)

    cohort_path = processed_dir / "cohort.parquet"
    if force or not cohort_path.exists():
        _copy(con, cohort_sql(origin, latest_dir, latest.ref_date), cohort_path)
    companies_path = processed_dir / "companies.parquet"
    if force or not companies_path.exists():
        # Descrições da natureza jurídica e da atividade, para o relatório ser legível.
        _copy(
            con,
            f"""
            SELECT f.*, n.descricao AS natureza_descricao, c.descricao AS cnae_descricao
            FROM ({company_features_sql(latest)}) f
            LEFT JOIN read_parquet('{latest_dir}/Naturezas/*.parquet') n
              ON n.codigo = f.natureza_juridica
            LEFT JOIN read_parquet('{latest_dir}/Cnaes/*.parquet') c
              ON c.codigo = f.cnae_fiscal_principal
            ORDER BY f.cnpj_basico
            """,
            companies_path,
        )
    # Metadados da foto usada no serviço, citados no relatório do agent.
    (processed_dir / "companies.json").write_text(
        json.dumps(
            {
                "data_referencia": latest.ref_date.isoformat(),
                "foto_receita": sources.LATEST.receita_month,
                "trimestre_pgfn": sources.LATEST.pgfn_quarter,
                "ano_ecf": latest.ecf_year,
            },
            indent=2,
        )
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    build(settings.data_dir / "interim", settings.data_dir / "processed")


if __name__ == "__main__":
    main()
