"""Confere se os números de uma resposta do agent vêm das ferramentas ou do analista.

É a verificação da regra "os números do relatório devem vir de dados e modelos, nunca ser
inventados pelo agent". Ela não impede o LLM de errar, mas torna o erro visível: a API devolve os
números sem origem e a interface avisa o analista.

Os dois lados (resposta e fontes) passam pela mesma extração, então formatos como "2,0%", "5.427"
ou "00.000.000/0001-91" são comparados de forma consistente.
"""

import json
import re
from collections.abc import Iterable
from typing import Any

# Números com separadores de milhar (ponto ou espaço) e/ou decimais (vírgula ou ponto).
_SPACES = "  \u202f"  # espaço comum, não separável e fino
_NUMBER = re.compile(rf"\d{{1,3}}(?:[{_SPACES}]\d{{3}})+(?:,\d+)?|\d+(?:[.,]\d+)*")
# Abreviações de grandeza depois do número ("R$ 120 bi", "506 milhões").
_SCALES = {
    "mil": 1e3,
    "mi": 1e6,
    "milhão": 1e6,
    "milhões": 1e6,
    "bi": 1e9,
    "bilhão": 1e9,
    "bilhões": 1e9,
    "tri": 1e12,
    "trilhão": 1e12,
    "trilhões": 1e12,
}
_SCALE = re.compile(r"\s*(" + "|".join(sorted(_SCALES, key=len, reverse=True)) + r")\b")
_SCALED_TOLERANCE = 0.01  # "R$ 506 mi" para 506.691.912: erro relativo de arredondamento
# Marcadores de lista numerada ("1. ", "2) ") no início da linha não são dados.
_LIST_MARKER = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)
# Contagens pequenas soltas ("os 3 fatores") são toleradas; com % ou "meses" ao lado, conferidas.
_SMALL_INT = 10
_UNIT_AFTER = re.compile(r"\s*(%|meses|mês|anos?\b)")
_TOLERANCE = 0.051  # a resposta pode arredondar para uma casa decimal


def _parse(token: str) -> list[float]:
    """Interpretações numéricas de um token (pode ser ambíguo entre milhar e decimal)."""
    token = re.sub(f"[{_SPACES}]", "", token)
    if "," in token and "." in token:  # padrão brasileiro: 1.234,5
        return [float(token.replace(".", "").replace(",", "."))]
    if "," in token:
        parts = token.split(",")
        if len(parts) == 2:
            return [float(token.replace(",", "."))]
        return [float(token.replace(",", ""))]
    if "." in token:
        parts = token.split(".")
        values = []
        if all(len(p) == 3 for p in parts[1:]):  # 5.427 ou 120.000.000
            values.append(float(token.replace(".", "")))
        if len(parts) == 2:  # 2.0 (decimal com ponto)
            values.append(float(token))
        return values or [float(token.replace(".", ""))]
    return [float(token)]


def _values_in_text(text: str) -> set[float]:
    found: set[float] = set()
    for match in _NUMBER.finditer(text):
        found.update(_parse(match.group()))
    return found


def _strings(obj: Any) -> Iterable[str]:
    """Todos os valores de um objeto JSON como texto (números inclusive)."""
    if isinstance(obj, dict):
        for value in obj.values():
            yield from _strings(value)
    elif isinstance(obj, list | tuple):
        for value in obj:
            yield from _strings(value)
    elif isinstance(obj, bool) or obj is None:
        return
    elif isinstance(obj, int | float):
        yield repr(obj)
        if isinstance(obj, float):
            yield f"{obj:.1f}"
            yield f"{100 * obj:.1f}"  # frações (0.1) também podem aparecer como percentual (10)
    else:
        yield str(obj)


def source_values(tool_outputs: Iterable[Any], user_messages: Iterable[str]) -> set[float]:
    values: set[float] = set()
    for output in tool_outputs:
        payload = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        values.update(_values_in_text(payload))
        if not isinstance(output, str):
            for text in _strings(output):
                values.update(_values_in_text(text))
    for message in user_messages:
        values.update(_values_in_text(message))
    return values


def ungrounded_numbers(
    response: str, tool_outputs: Iterable[Any], user_messages: Iterable[str] = ()
) -> list[str]:
    """Números da resposta que não aparecem nas saídas das ferramentas nem nas mensagens."""
    allowed = source_values(tool_outputs, user_messages)
    text = _LIST_MARKER.sub(" ", response)
    missing = []
    for match in _NUMBER.finditer(text):
        token = match.group()
        candidates = _parse(token)
        if scale := _SCALE.match(text, match.end()):
            scaled = [c * _SCALES[scale.group(1)] for c in candidates]
            if any(abs(s - a) <= _SCALED_TOLERANCE * a for s in scaled for a in allowed if a):
                continue
        has_unit = bool(_UNIT_AFTER.match(text, match.end()))
        if not has_unit and all(c.is_integer() and c <= _SMALL_INT for c in candidates):
            continue
        if not any(abs(c - a) <= _TOLERANCE for c in candidates for a in allowed):
            missing.append(token)
    return list(dict.fromkeys(missing))
