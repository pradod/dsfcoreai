"""Normalização e validação de CNPJ, inclusive no formato alfanumérico.

Desde julho de 2026 a Receita emite CNPJs alfanuméricos: as 12 primeiras posições podem ter
letras maiúsculas e os 2 dígitos verificadores continuam numéricos. O cálculo do verificador é o
mesmo módulo 11 de sempre, com cada caractere valendo o seu código ASCII menos 48 (dígitos de 0 a 9,
letras de A=17 a Z=42), então CNPJs numéricos antigos continuam válidos.
"""

import re
from dataclasses import dataclass

_WEIGHTS_FIRST = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_WEIGHTS_SECOND = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_SEPARATORS = re.compile(r"[.\-/\s]")
_FORMAT = re.compile(r"[0-9A-Z]{12}[0-9]{2}")


class InvalidCNPJError(ValueError):
    """O texto informado não é um CNPJ válido."""


@dataclass(frozen=True)
class CNPJ:
    value: str  # 14 caracteres, sem pontuação

    @property
    def basico(self) -> str:
        """Raiz de 8 caracteres que identifica a empresa (comum à matriz e às filiais)."""
        return self.value[:8]

    @property
    def ordem(self) -> str:
        return self.value[8:12]

    @property
    def is_matriz(self) -> bool:
        return self.ordem == "0001"

    @property
    def formatted(self) -> str:
        v = self.value
        return f"{v[:2]}.{v[2:5]}.{v[5:8]}/{v[8:12]}-{v[12:]}"


def _check_digit(chars: str, weights: tuple[int, ...]) -> str:
    total = sum((ord(c) - 48) * w for c, w in zip(chars, weights, strict=True))
    remainder = total % 11
    return "0" if remainder < 2 else str(11 - remainder)


def parse_cnpj(text: str) -> CNPJ:
    """Converte o texto do analista num CNPJ válido ou levanta `InvalidCNPJError`.

    Aceita com ou sem pontuação e completa com zeros à esquerda CNPJs numéricos digitados sem
    eles (ex.: "191" para o Banco do Brasil).
    """
    raw = _SEPARATORS.sub("", text.strip().upper())
    if raw.isdigit() and len(raw) < 14:
        raw = raw.zfill(14)
    if not _FORMAT.fullmatch(raw):
        raise InvalidCNPJError(
            f"'{text}' não tem o formato de um CNPJ "
            "(14 caracteres: 12 letras ou números e 2 dígitos)"
        )
    if len(set(raw)) == 1:
        raise InvalidCNPJError(f"'{text}' não é um CNPJ válido (todos os caracteres iguais)")
    first = _check_digit(raw[:12], _WEIGHTS_FIRST)
    second = _check_digit(raw[:12] + first, _WEIGHTS_SECOND)
    if raw[12:] != first + second:
        raise InvalidCNPJError(
            f"'{text}' não é um CNPJ válido (dígitos verificadores não conferem)"
        )
    return CNPJ(raw)
