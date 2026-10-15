"""Preparo completo dos dados: download e conversão para Parquet.

Uso:
    python -m consig_risk.data

Idempotente: o que já foi baixado ou convertido é pulado, então rodar de novo é rápido.
Na primeira vez leva cerca de 40 minutos (≈32 min de download com ~5 MB/s e ~6 min de conversão).
"""

from consig_risk.data import build, build_extras, download


def main() -> None:
    download.main([])
    build.main([])
    build_extras.main([])


if __name__ == "__main__":
    main()
