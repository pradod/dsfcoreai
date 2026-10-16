"""Preparo completo dos dados: download, conversão para Parquet e datasets processados.

Uso:
    python -m consig_risk.data

Idempotente: o que já foi baixado ou convertido é pulado, então rodar de novo é rápido.
Na primeira vez leva cerca de 45 minutos (≈32 min de download com ~5 MB/s, ~6 min de conversão
e ~4 min para gerar a coorte de treino e a tabela de empresas usada nas consultas).
"""

from consig_risk.data import build, build_extras, download
from consig_risk.features import build as features_build


def main() -> None:
    download.main([])
    build.main([])
    build_extras.main([])
    features_build.main()


if __name__ == "__main__":
    main()
