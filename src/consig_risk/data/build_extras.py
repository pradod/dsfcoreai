"""Converte as fontes complementares (Regimes Tributários e PGFN) em Parquet.

Uso:
    python -m consig_risk.data.build_extras

Saídas:
    data/interim/regimes/regimes.parquet           (cnpj_basico, ano, forma_de_tributacao)
    data/interim/pgfn/<trimestre>/<origem>.parquet (só pessoa jurídica; sem nome do devedor)
"""

import argparse
import logging
import tempfile
import time
import zipfile
from pathlib import Path

import duckdb

from consig_risk.config import get_settings
from consig_risk.data import sources
from consig_risk.data.build import extract_clean

logger = logging.getLogger(__name__)

# arquivo -> (origem do débito, coluna que traz a natureza do débito naquele arquivo)
PGFN_ORIGINS = {
    "Dados_abertos_FGTS.zip": ("fgts", "RECEITA_PRINCIPAL"),
    "Dados_abertos_Previdenciario.zip": ("previdenciario", "TIPO_CREDITO"),
    "Dados_abertos_Nao_Previdenciario.zip": ("nao_previdenciario", "RECEITA_PRINCIPAL"),
}


def _digits(col: str) -> str:
    return f"regexp_replace({col}, '[^0-9]', '', 'g')"


def _extract_all(zip_path: Path, tmp_dir: Path) -> list[Path]:
    """Extrai todos os CSVs de um zip, limpando bytes de controle."""
    paths: list[Path] = []
    with zipfile.ZipFile(zip_path) as archive:
        for i, member in enumerate(archive.infolist()):
            path = tmp_dir / f"{zip_path.stem}_{i}.csv"
            with archive.open(member) as src, path.open("wb") as dst:
                extract_clean(src, dst)
            paths.append(path)
    return paths


def _copy(query: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".tmp")
    partial.unlink(missing_ok=True)
    try:
        duckdb.sql(f"COPY ({query}) TO '{partial}' (FORMAT parquet, COMPRESSION zstd)")
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(dest)
    return dest


def build_regimes(raw_dir: Path, out_dir: Path) -> Path:
    """Uma linha por (CNPJ básico, ano, forma de tributação) declarada na ECF."""
    dest = out_dir / "regimes" / "regimes.parquet"
    if dest.exists():
        logger.info("já convertido: %s", dest)
        return dest
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
        csvs = [
            p
            for name in sources.REGIMES_FILES
            for p in _extract_all(raw_dir / "regimes" / name, Path(tmp))
        ]
        files = ", ".join(f"'{p}'" for p in csvs)
        return _copy(
            f"""
            SELECT DISTINCT
              left({_digits("cnpj")}, 8) AS cnpj_basico,
              try_cast(ano AS SMALLINT) AS ano,
              trim(forma_de_tributacao) AS forma_de_tributacao
            FROM read_csv([{files}], header=true, all_varchar=true, union_by_name=true)
            WHERE coalesce(trim(cnpj_da_scp), '0') = '0'  -- só a própria empresa, não SCPs
            """,
            dest,
        )


def build_pgfn_quarter(raw_dir: Path, out_dir: Path, quarter: str) -> list[Path]:
    """Inscrições em dívida ativa de pessoas jurídicas num trimestre, por origem do débito.

    Devedores pessoa física e o nome do devedor são descartados aqui (LGPD). Datas de inscrição
    anteriores a 1900 são marcadores de "desconhecida" na origem e viram nulo.
    """
    outputs: list[Path] = []
    for zip_name, (origin, natureza_col) in PGFN_ORIGINS.items():
        dest = out_dir / "pgfn" / quarter / f"{origin}.parquet"
        if dest.exists():
            logger.info("já convertido: %s", dest)
            outputs.append(dest)
            continue
        started = time.monotonic()
        with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
            csvs = _extract_all(raw_dir / "pgfn" / quarter / zip_name, Path(tmp))
            files = ", ".join(f"'{p}'" for p in csvs)
            outputs.append(
                _copy(
                    f"""
                    SELECT
                      left({_digits("CPF_CNPJ")}, 8) AS cnpj_basico,
                      {_digits("CPF_CNPJ")} AS cnpj,
                      upper(TIPO_DEVEDOR) AS tipo_devedor,
                      TIPO_SITUACAO_INSCRICAO AS tipo_situacao_inscricao,
                      SITUACAO_INSCRICAO AS situacao_inscricao,
                      {natureza_col} AS natureza_debito,
                      CASE WHEN d >= DATE '1900-01-01' THEN d END AS data_inscricao,
                      INDICADOR_AJUIZADO = 'SIM' AS ajuizado,
                      try_cast(VALOR_CONSOLIDADO AS DOUBLE) AS valor_consolidado,
                      '{origin}' AS origem
                    FROM (
                      SELECT *, try_strptime(DATA_INSCRICAO, '%d/%m/%Y')::DATE AS d
                      FROM read_csv(
                        [{files}], delim=';', header=true, encoding='latin-1',
                        all_varchar=true, union_by_name=true
                      )
                    )
                    WHERE TIPO_PESSOA = 'Pessoa jurídica'
                    """,
                    dest,
                )
            )
        logger.info("pgfn/%s/%s em %.1fs", quarter, origin, time.monotonic() - started)
    return outputs


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--quarters",
        nargs="+",
        default=[sources.ORIGIN.pgfn_quarter, sources.LATEST.pgfn_quarter],
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    raw_dir, out_dir = settings.data_dir / "raw", settings.data_dir / "interim"
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    build_regimes(raw_dir, out_dir)
    for quarter in args.quarters:
        build_pgfn_quarter(raw_dir, out_dir, quarter)
    logger.info("concluído em %.1f min", (time.monotonic() - started) / 60)


if __name__ == "__main__":
    main()
