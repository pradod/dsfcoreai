"""Acesso aos dados de uma empresa na foto mais recente.

A interface `CompanyRepository` isola o serviço da forma de armazenamento. Hoje é um Parquet
ordenado por CNPJ básico, lido com DuckDB (~30 ms por consulta, sem servidor). Em produção, a mesma
interface poderia ser atendida por um banco relacional ou por uma API cadastral, sem mudar o agent.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import duckdb


@dataclass(frozen=True)
class SnapshotInfo:
    data_referencia: str
    foto_receita: str
    trimestre_pgfn: str
    ano_ecf: int


class CompanyRepository(Protocol):
    def get(self, cnpj_basico: str) -> dict[str, Any] | None:
        """Features e dados cadastrais da empresa, ou None se o CNPJ não existe na base."""
        ...

    @property
    def snapshot(self) -> SnapshotInfo: ...


class ParquetCompanyRepository:
    def __init__(self, companies_path: Path) -> None:
        if not companies_path.exists():
            raise FileNotFoundError(
                f"{companies_path} não existe; "
                "rode `python -m consig_risk.data` para preparar os dados"
            )
        self._path = companies_path
        self._con = duckdb.connect()
        meta = json.loads(companies_path.with_suffix(".json").read_text())
        self._snapshot = SnapshotInfo(**meta)

    @property
    def snapshot(self) -> SnapshotInfo:
        return self._snapshot

    def get(self, cnpj_basico: str) -> dict[str, Any] | None:
        # Um cursor por consulta: a conexão pode ser compartilhada entre threads da API.
        cursor = self._con.cursor()
        rel = cursor.execute(
            f"SELECT * FROM read_parquet('{self._path}') WHERE cnpj_basico = ?", [cnpj_basico]
        )
        row = rel.fetchone()
        if row is None:
            return None
        columns = [d[0] for d in rel.description]
        return dict(zip(columns, row, strict=True))
