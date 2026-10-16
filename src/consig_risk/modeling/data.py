"""Carga da coorte, divisão treino/validação/teste e expansão em períodos."""

from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from consig_risk.modeling.spec import (
    PIECE_LENGTH_AFTER_LAST,
    PIECE_STARTS,
    SOURCE_COLUMNS,
)


@dataclass(frozen=True)
class Split:
    """Faixa de baldes de amostragem (0–999, por hash do CNPJ básico): [start, stop)."""

    name: str
    start: int
    stop: int

    @property
    def fraction(self) -> float:
        return (self.stop - self.start) / 1000


# Baldes disjuntos e fixos: quem clonar o repositório obtém exatamente as mesmas empresas.
TRAIN = Split("treino", 0, 200)  # 20% (~1,9 mi empresas)
VALIDATION = Split("validação", 800, 850)  # 5%: early stopping e escolha do modelo
TEST = Split("teste", 900, 1000)  # 10%: só para reportar o resultado final


def load_cohort(cohort_path: Path, split: Split, limit_buckets: int | None = None) -> pd.DataFrame:
    """Empresas da coorte num split. `limit_buckets` reduz o split (ex.: para o Cox, mais lento)."""
    stop = split.start + limit_buckets if limit_buckets else split.stop
    columns = ", ".join(("cnpj_basico", *SOURCE_COLUMNS, "evento", "tipo_evento", "duracao_meses"))
    return duckdb.sql(
        f"SELECT {columns} FROM read_parquet('{cohort_path}') "
        f"WHERE amostra >= {split.start} AND amostra < {stop} ORDER BY cnpj_basico"
    ).df()


def expand_pieces(firms: pd.DataFrame, event_col: str = "evento") -> pd.DataFrame:
    """Uma linha por (empresa, período) com exposição em meses e indicador de evento.

    O último período vai do início dele até o fim do acompanhamento da empresa.
    """
    duration = firms["duracao_meses"].to_numpy()
    event = firms[event_col].to_numpy(dtype=bool)
    parts = []
    bounds = [*PIECE_STARTS[1:], np.inf]
    for periodo, (start, end) in enumerate(zip(PIECE_STARTS, bounds, strict=True), start=1):
        at_risk = duration > start
        exposure = np.clip(np.minimum(duration, end) - start, 0, None)
        piece = firms.loc[at_risk].copy()
        piece["periodo"] = periodo
        piece["idade_no_periodo"] = piece["idade_meses"] + start
        piece["exposicao"] = exposure[at_risk]
        piece["eventos"] = (event & (duration <= end))[at_risk].astype(float)
        parts.append(piece)
    return pd.concat(parts, ignore_index=True)


def prediction_pieces(firms: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Períodos que cobrem [0, horizon] para prever a sobrevivência de cada empresa.

    Depois do último período do treino, novos blocos de 12 meses reutilizam o último período
    (`periodo` fica no valor máximo) com a idade da empresa avançada.
    """
    starts = list(PIECE_STARTS)
    while starts[-1] + PIECE_LENGTH_AFTER_LAST < horizon:
        starts.append(starts[-1] + PIECE_LENGTH_AFTER_LAST)
    ends = [*starts[1:], horizon]
    last_periodo = len(PIECE_STARTS)
    parts = []
    for i, (start, end) in enumerate(zip(starts, ends, strict=True)):
        if start >= horizon:
            break
        piece = firms.copy()
        piece["_firm"] = np.arange(len(firms))
        piece["periodo"] = min(i + 1, last_periodo)
        piece["idade_no_periodo"] = piece["idade_meses"] + start
        piece["exposicao"] = float(min(end, horizon) - start)
        parts.append(piece)
    return pd.concat(parts, ignore_index=True)
