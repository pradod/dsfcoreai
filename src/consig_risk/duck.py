"""Conexão DuckDB usada pelo pipeline de dados.

Tem teto de memória (`DUCKDB_MEMORY_LIMIT`, padrão 4 GB) e grava temporários no volume de dados.
Consultas grandes (como ordenar as 70 milhões de empresas) despejam dados temporários em disco. Por
padrão o DuckDB usa `.tmp/` no diretório atual, que no contêiner fica no disco virtual do Docker e
pode lotá-lo. Aqui os temporários vão para `data/.duckdb_tmp`, que é o mesmo volume dos dados.
"""

import duckdb

from consig_risk.config import get_settings


def connect() -> duckdb.DuckDBPyConnection:
    settings = get_settings()
    temp_dir = settings.data_dir / ".duckdb_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET temp_directory = '{temp_dir}'")
    # Sem teto explícito o DuckDB usa até 80% da memória que enxerga e pode ser morto pelo Docker.
    con.execute(f"SET memory_limit = '{settings.duckdb_memory_limit}'")
    # A ordem de inserção não importa nas conversões (quem precisa de ordem usa ORDER BY), e
    # liberá-la reduz bastante a memória usada.
    con.execute("SET preserve_insertion_order = false")
    return con
