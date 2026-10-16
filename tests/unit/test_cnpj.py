import pytest

from consig_risk.risk.cnpj import InvalidCNPJError, parse_cnpj


@pytest.mark.parametrize(
    "text",
    ["00.000.000/0001-91", "00000000000191", " 00.000.000/0001-91 ", "191"],
)
def test_parses_numeric_cnpj_with_or_without_punctuation(text: str) -> None:
    cnpj = parse_cnpj(text)

    assert cnpj.value == "00000000000191"
    assert cnpj.basico == "00000000"
    assert cnpj.is_matriz
    assert cnpj.formatted == "00.000.000/0001-91"


def test_parses_alphanumeric_cnpj_official_example() -> None:
    # Exemplo publicado pela Receita para o CNPJ alfanumérico (em vigor desde jul/2026).
    cnpj = parse_cnpj("12.abc.345/01de-35")

    assert cnpj.value == "12ABC34501DE35"
    assert cnpj.basico == "12ABC345"
    assert not cnpj.is_matriz


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("00.000.000/0001-92", "verificadores"),
        ("12.ABC.345/01DE-34", "verificadores"),
        ("11.111.111/1111-11", "iguais"),
        ("banco do brasil", "formato"),
        ("00.000.000/0001-9A", "formato"),  # verificadores são sempre numéricos
        ("", "formato"),
    ],
)
def test_rejects_invalid_cnpj_with_a_clear_reason(text: str, reason: str) -> None:
    with pytest.raises(InvalidCNPJError, match=reason):
        parse_cnpj(text)
