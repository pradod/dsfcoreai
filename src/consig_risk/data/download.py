"""Baixa os dados brutos de todas as fontes para `data/raw/`.

Uso:
    python -m consig_risk.data.download                # fotos de origem e de desfecho
    python -m consig_risk.data.download --only receita --months 2026-10 --tables Simples

É idempotente: arquivos completos são pulados e parciais são retomados.
"""

import argparse
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from pathlib import Path

import httpx

from consig_risk.config import get_settings
from consig_risk.data import sources
from consig_risk.data.remote import NextcloudShare, RemoteFile, download

logger = logging.getLogger(__name__)

Job = Callable[[], Path]


def receita_jobs(
    client: httpx.Client, raw_dir: Path, months: list[str], tables: tuple[str, ...] | None
) -> list[Job]:
    share = NextcloudShare(sources.RECEITA_BASE_URL, sources.CNPJ_SHARE_TOKEN, client)
    jobs: list[Job] = []
    wanted = set(sources.receita_files(tables))
    for month in months:
        available = {f.name: f for f in share.list(month)}
        missing = wanted - available.keys()
        if missing:
            raise FileNotFoundError(f"Receita {month}: arquivos ausentes no servidor: {missing}")
        for name in sorted(wanted):
            remote = available[name]
            dest = raw_dir / "receita" / month / name
            jobs.append(_share_job(client, share, remote, dest))
    return jobs


def regimes_jobs(client: httpx.Client, raw_dir: Path) -> list[Job]:
    share = NextcloudShare(sources.RECEITA_BASE_URL, sources.REGIMES_SHARE_TOKEN, client)
    available = {f.name: f for f in share.list()}
    return [
        _share_job(client, share, available[name], raw_dir / "regimes" / name)
        for name in sources.REGIMES_FILES
    ]


def pgfn_jobs(client: httpx.Client, raw_dir: Path, quarters: list[str]) -> list[Job]:
    jobs: list[Job] = []
    for quarter in quarters:
        for name in sources.PGFN_FILES:
            url = f"{sources.PGFN_BASE_URL}/{quarter}/{name}"
            size = _content_length(client, url)
            dest = raw_dir / "pgfn" / quarter / name
            jobs.append(partial(download, client, url, dest, expected_size=size))
    return jobs


def _share_job(client: httpx.Client, share: NextcloudShare, remote: RemoteFile, dest: Path) -> Job:
    return partial(
        download, client, share.url_for(remote), dest, expected_size=remote.size, auth=share.auth
    )


def _content_length(client: httpx.Client, url: str) -> int | None:
    response = client.head(url)
    response.raise_for_status()
    length = response.headers.get("content-length")
    return int(length) if length else None


def run(jobs: list[Job], workers: int) -> None:
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(job) for job in jobs]
        for done, future in enumerate(as_completed(futures), start=1):
            path = future.result()
            logger.info("[%d/%d] %s", done, len(jobs), path)
    logger.info("concluído em %.1f min", (time.monotonic() - started) / 60)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--only", choices=["receita", "regimes", "pgfn"], action="append")
    parser.add_argument(
        "--months",
        nargs="+",
        default=[sources.ORIGIN.receita_month, sources.LATEST.receita_month],
        help="fotos mensais da Receita (AAAA-MM)",
    )
    parser.add_argument("--tables", nargs="+", help="tabelas da Receita (padrão: todas)")
    parser.add_argument(
        "--quarters",
        nargs="+",
        default=[sources.ORIGIN.pgfn_quarter, sources.LATEST.pgfn_quarter],
        help="fotos trimestrais da PGFN (AAAA_trimestre_NN)",
    )
    parser.add_argument("--workers", type=int, default=3, help="downloads simultâneos")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raw_dir = get_settings().data_dir / "raw"
    selected = set(args.only or ["receita", "regimes", "pgfn"])
    timeout = httpx.Timeout(120.0, connect=30.0)

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        jobs: list[Job] = []
        if "receita" in selected:
            tables = tuple(args.tables) if args.tables else None
            jobs += receita_jobs(client, raw_dir, args.months, tables)
        if "regimes" in selected:
            jobs += regimes_jobs(client, raw_dir)
        if "pgfn" in selected:
            jobs += pgfn_jobs(client, raw_dir, args.quarters)
        logger.info("%d arquivos a verificar/baixar em %s", len(jobs), raw_dir)
        run(jobs, args.workers)


if __name__ == "__main__":
    main()
