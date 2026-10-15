"""Catálogo das fontes públicas usadas e de onde cada arquivo vem.

Ver docs/adr/0002-fontes-de-dados.md para o motivo de cada escolha.
"""

from dataclasses import dataclass

RECEITA_BASE_URL = "https://arquivos.receitafederal.gov.br"

# Tokens dos links públicos publicados em dados.gov.br ("Acessar o recurso").
CNPJ_SHARE_TOKEN = "YggdBLfdninEJX9"  # Inscrições no CNPJ (fotos mensais)
REGIMES_SHARE_TOKEN = "MPPfFit7g7zdA8C"  # Regimes Tributários (ECF)

PGFN_BASE_URL = "https://dadosabertos.pgfn.gov.br"
PGFN_FILES = (
    "Dados_abertos_FGTS.zip",
    "Dados_abertos_Previdenciario.zip",
    "Dados_abertos_Nao_Previdenciario.zip",
)

# Tabelas da Receita: as particionadas em 10 partes (0-9) e as de domínio (arquivo único).
PARTITIONED_TABLES = ("Empresas", "Estabelecimentos", "Socios")
SINGLE_FILE_TABLES = (
    "Simples",
    "Cnaes",
    "Motivos",
    "Municipios",
    "Naturezas",
    "Paises",
    "Qualificacoes",
)

REGIMES_FILES = (
    "entidades-lucro-real.zip",
    "entidades-lucro-presumido.zip",
    "entidades-lucro-arbitrado.zip",
    "entidades-imunes-e-isentas.zip",
)


@dataclass(frozen=True)
class Snapshot:
    """Par de fotos alinhadas no tempo: Receita (mensal) e PGFN (trimestral).

    A foto da PGFN é sempre a última fechada *antes* da foto da Receita, para que nenhuma
    informação de dívida seja posterior à data de referência das features.
    """

    receita_month: str  # AAAA-MM
    pgfn_quarter: str  # AAAA_trimestre_NN


# Foto de origem (features) e foto de desfecho (situação observada ~41 meses depois).
# 2023-05 é a foto mais antiga disponível no compartilhamento da Receita.
ORIGIN = Snapshot(receita_month="2023-05", pgfn_quarter="2023_trimestre_01")
LATEST = Snapshot(receita_month="2026-10", pgfn_quarter="2026_trimestre_02")


def receita_files(tables: tuple[str, ...] | None = None) -> list[str]:
    """Nomes dos arquivos .zip de uma foto da Receita, para as tabelas pedidas (padrão: todas)."""
    wanted = tables or PARTITIONED_TABLES + SINGLE_FILE_TABLES
    names: list[str] = []
    for table in wanted:
        if table in PARTITIONED_TABLES:
            names.extend(f"{table}{part}.zip" for part in range(10))
        elif table in SINGLE_FILE_TABLES:
            names.append(f"{table}.zip")
        else:
            raise ValueError(f"tabela desconhecida da Receita: {table}")
    return names
