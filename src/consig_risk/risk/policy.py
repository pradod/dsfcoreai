"""Política de risco aceitável: transforma a curva de risco do modelo num veredito.

Regra: conceder um consignado de *t* meses se a probabilidade de o empregador sair da situação
ativa até *t* for no máximo `limite_risco`. O prazo máximo é o maior prazo da grade que respeita o
limite, sem passar de `prazo_maximo_meses`. Justificativa e simulação em
docs/adr/0004-politica-de-risco.md.

Os parâmetros vêm de variáveis de ambiente (prefixo RISK_), porque o limite é decisão do negócio e
não deve exigir mudança de código.
"""

from dataclasses import dataclass

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RiskPolicy(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RISK_", env_file=".env", extra="ignore")

    limite_risco: float = Field(0.10, gt=0, lt=1, description="risco máximo aceito no prazo")
    prazo_maximo_meses: int = Field(
        36, ge=6, le=96, description="maior prazo que a política aprova"
    )
    passo_prazo_meses: int = Field(6, ge=1, description="granularidade dos prazos avaliados")
    # Limites superiores das faixas de nível de risco (o último é "muito alto").
    faixa_baixo: float = 0.05
    faixa_moderado: float = 0.10
    faixa_alto: float = 0.20

    def prazos(self) -> tuple[int, ...]:
        step = self.passo_prazo_meses
        return tuple(range(step, self.prazo_maximo_meses + 1, step))

    def nivel(self, risco: float) -> str:
        if risco <= self.faixa_baixo:
            return "baixo"
        if risco <= self.faixa_moderado:
            return "moderado"
        if risco <= self.faixa_alto:
            return "alto"
        return "muito alto"


def pct(value: float, decimals: int = 1) -> str:
    """Percentual no formato brasileiro (ex.: 0.021 -> "2,1%")."""
    return f"{100 * value:.{decimals}f}%".replace(".", ",")


@dataclass(frozen=True)
class TermDecision:
    prazo_meses: int
    risco: float
    conceder: bool
    motivo: str


def decide_term(policy: RiskPolicy, prazo_meses: int, risco: float) -> TermDecision:
    """Veredito para um prazo pedido, dado o risco acumulado nesse prazo."""
    if prazo_meses > policy.prazo_maximo_meses:
        return TermDecision(
            prazo_meses,
            risco,
            False,
            f"o prazo pedido passa do máximo da política ({policy.prazo_maximo_meses} meses), "
            "que é o horizonte em que o modelo foi validado",
        )
    if risco <= policy.limite_risco:
        return TermDecision(
            prazo_meses,
            risco,
            True,
            f"risco de {pct(risco)} até o fim do prazo, "
            f"dentro do limite de {pct(policy.limite_risco, 0)}",
        )
    return TermDecision(
        prazo_meses,
        risco,
        False,
        f"risco de {pct(risco)} até o fim do prazo, "
        f"acima do limite de {pct(policy.limite_risco, 0)}",
    )


def max_term(policy: RiskPolicy, risk_by_term: dict[int, float]) -> int | None:
    """Maior prazo da grade com risco dentro do limite, ou None se nem o menor prazo cabe.

    O risco acumulado nunca diminui com o prazo, então basta achar o último prazo aceito.
    """
    accepted = [t for t in policy.prazos() if risk_by_term[t] <= policy.limite_risco]
    return max(accepted) if accepted else None
