"""Treina o modelo usado pelo agent e grava o artefato em `artifacts/model/`.

Uso:
    python -m consig_risk.modeling.train

O modelo é o exponencial por partes com LightGBM, escolhido na comparação do notebook
02_modelagem. Treina no split de treino (20% das empresas), para pelo split de validação (5%) e
registra as métricas de validação no artefato. O split de teste (10%) não é tocado aqui: ele é
usado só no notebook, para reportar o resultado final.

Determinístico: mesmos dados e mesma versão das bibliotecas geram o mesmo modelo. Se o artefato
já foi treinado com a mesma coorte (mesmo SHA-256), o treino é pulado; `--force` treina de novo.
Leva cerca de 2 minutos numa máquina com 4 núcleos.
"""

import argparse
import hashlib
import importlib.metadata
import json
import logging
import time
from pathlib import Path
from typing import Any

import duckdb

from consig_risk.config import get_settings
from consig_risk.data import sources
from consig_risk.modeling import data
from consig_risk.modeling.artifact import RiskModel
from consig_risk.modeling.metrics import concordance, horizon_metrics
from consig_risk.modeling.models import PiecewiseGBM, fit_categories
from consig_risk.modeling.spec import EVALUATION_HORIZONS, FEATURES

logger = logging.getLogger(__name__)


def _versions() -> dict[str, str]:
    return {
        p: importlib.metadata.version(p)
        for p in ("lightgbm", "numpy", "pandas", "scikit-learn", "duckdb")
    }


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def train(cohort_path: Path, model_dir: Path) -> RiskModel:
    started = time.monotonic()
    train_firms = data.load_cohort(cohort_path, data.TRAIN)
    valid_firms = data.load_cohort(cohort_path, data.VALIDATION)
    logger.info("empresas: treino %d, validação %d", len(train_firms), len(valid_firms))

    categories = fit_categories(train_firms)
    gbm = PiecewiseGBM().fit(
        data.expand_pieces(train_firms),
        categories,
        valid_pieces=data.expand_pieces(valid_firms),
    )
    logger.info("LightGBM: %d árvores (early stopping)", gbm.booster.best_iteration)

    survival = gbm.survival(valid_firms, EVALUATION_HORIZONS)
    metrics = horizon_metrics(valid_firms, survival, EVALUATION_HORIZONS)
    reference = duckdb.sql(
        f"SELECT min(idade_meses), max(idade_meses) FROM read_parquet('{cohort_path}')"
    ).fetchone()
    metadata: dict[str, Any] = {
        "modelo": gbm.name,
        "evento": "empresa sai da situação cadastral ativa (baixada, inapta, suspensa ou nula)",
        "foto_origem": sources.ORIGIN.receita_month,
        "foto_desfecho": sources.LATEST.receita_month,
        "acompanhamento_meses": max(EVALUATION_HORIZONS),
        "horizonte_maximo_validado_meses": 36,
        "splits": {s.name: [s.start, s.stop] for s in (data.TRAIN, data.VALIDATION, data.TEST)},
        "empresas_treino": len(train_firms),
        "empresas_validacao": len(valid_firms),
        "idade_meses_no_treino": list(reference) if reference else None,
        # Faixa (p0,1–p99,9) de cada feature numérica no treino. Acima disso, as árvores tratam o
        # valor como o maior já visto: o agent avisa quando uma empresa cai fora da faixa.
        "faixas_treino": {
            f.name: {
                "p001": float(train_firms[f.name].quantile(0.001)),
                "p999": float(train_firms[f.name].quantile(0.999)),
            }
            for f in FEATURES
            if f.kind == "numeric" and f.name in train_firms
        },
        "validacao": {
            "c_index": concordance(valid_firms, survival[:, -1]),
            "por_horizonte": metrics.to_dict(orient="records"),
        },
        "cohort_sha256": _file_sha256(cohort_path),
        "versoes": _versions(),
    }
    model = RiskModel(gbm, metadata)
    model.save(model_dir)
    logger.info("validação:\n%s", metrics.to_string(index=False))
    logger.info("artefato gravado em %s em %.1f min", model_dir, (time.monotonic() - started) / 60)
    return model


def is_up_to_date(cohort_path: Path, model_dir: Path) -> bool:
    """O artefato existe e foi treinado com exatamente esta coorte?"""
    metadata_path = model_dir / "metadata.json"
    if not (model_dir / "model.txt").exists() or not metadata_path.exists():
        return False
    trained_on = json.loads(metadata_path.read_text()).get("cohort_sha256")
    return bool(trained_on == _file_sha256(cohort_path))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Treina o modelo usado pelo agent.")
    parser.add_argument(
        "--force", action="store_true", help="treina mesmo se já estiver atualizado"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    cohort_path = settings.data_dir / "processed" / "cohort.parquet"
    model_dir = settings.artifacts_dir / "model"
    if not cohort_path.exists():
        raise SystemExit(
            f"{cohort_path} não existe: rode o preparo de dados antes "
            "(`python -m consig_risk.data` ou o serviço data-prep do docker compose)"
        )
    if not args.force and is_up_to_date(cohort_path, model_dir):
        logger.info(
            "modelo já treinado com esta coorte em %s; use --force para treinar de novo", model_dir
        )
        return
    train(cohort_path, model_dir)


if __name__ == "__main__":
    main()
