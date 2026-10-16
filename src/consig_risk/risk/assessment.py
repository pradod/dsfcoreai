"""Avaliação de risco de um empregador: junta dados, modelo e política num relatório estruturado.

Todo número do relatório sai daqui. O agent só narra o que este serviço devolve; ele nunca
calcula nem estima valores por conta própria.
"""

import datetime as dt
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import pandas as pd

from consig_risk.modeling.artifact import RiskModel
from consig_risk.modeling.spec import LABELS, SOURCE_COLUMNS
from consig_risk.risk.cnpj import CNPJ, parse_cnpj
from consig_risk.risk.policy import RiskPolicy, decide_term, max_term, pct
from consig_risk.risk.repository import CompanyRepository

Resultado = Literal["avaliada", "nao_elegivel", "fora_do_escopo"]

SITUACOES = {1: "nula", 2: "ativa", 3: "suspensa", 4: "inapta", 8: "baixada"}
PORTES = {
    "01": "microempresa",
    "03": "empresa de pequeno porte",
    "05": "média ou grande empresa",
    "00": "não informado",
}
REGIMES = {
    "simples": "Simples Nacional",
    "sem_ecf": "não entregou a ECF estando fora do Simples",
    "real": "Lucro Real",
    "presumido": "Lucro Presumido",
    "arbitrado": "Lucro Arbitrado",
    "imune_isenta": "imune ou isenta de IRPJ",
}
GRUPOS_FORA_DO_ESCOPO = {
    "1": "administração pública (tem consignado próprio)",
    "4": "pessoa física com CNPJ (ex.: produtor rural, candidato a cargo político)",
    "5": "organismo internacional",
}
SECOES_CNAE = {
    "A": "agropecuária",
    "B": "indústria extrativa",
    "C": "indústria de transformação",
    "D": "eletricidade e gás",
    "E": "água, esgoto e resíduos",
    "F": "construção",
    "G": "comércio",
    "H": "transporte e armazenagem",
    "I": "alojamento e alimentação",
    "J": "informação e comunicação",
    "K": "atividades financeiras",
    "L": "atividades imobiliárias",
    "M": "atividades profissionais e científicas",
    "N": "atividades administrativas",
    "O": "administração pública",
    "P": "educação",
    "Q": "saúde e serviços sociais",
    "R": "artes, cultura e esporte",
    "S": "outros serviços",
    "T": "serviços domésticos",
    "U": "organismos internacionais",
}
MIN_RELIABLE_AGE_MONTHS = 36
REFERENCE_HORIZON = 12  # horizonte do "nível de risco", comparável entre empresas

LIMITACOES = (
    "O risco medido é o de o empregador sair da situação cadastral ativa (baixa, inaptidão, "
    "suspensão ou nulidade), o que interrompe o desconto em folha. Não é o risco de "
    "inadimplência do funcionário.",
    "Não considera a demissão do funcionário, nem faturamento ou folha de pagamento da empresa: "
    "esses dados não são públicos.",
    "O modelo ordena bem o risco (AUC de cerca de 0,74), mas não prevê com certeza quais empresas "
    "vão fechar. As probabilidades são calibradas até 36 meses.",
)


class CompanyNotFoundError(LookupError):
    """O CNPJ é válido, mas não existe na base da Receita usada pelo sistema."""

    def __init__(self, cnpj: CNPJ, data_referencia: str) -> None:
        data = dt.date.fromisoformat(data_referencia).strftime("%d/%m/%Y")
        super().__init__(f"o CNPJ {cnpj.formatted} não foi encontrado na base da Receita de {data}")
        self.cnpj = cnpj


@dataclass(frozen=True)
class CompanyInfo:
    cnpj: str
    cnpj_basico: str
    razao_social: str | None
    situacao_cadastral: str
    data_situacao_cadastral: str | None
    data_inicio_atividade: str | None
    idade: str
    porte: str
    natureza_juridica: str
    atividade_principal: str
    uf: str | None
    regime_tributario: str
    filiais_ativas: int
    dividas_ativas_pgfn: list[str]


@dataclass(frozen=True)
class RiskPoint:
    prazo_meses: int
    risco_pct: float
    dentro_do_limite: bool
    extrapolado: bool


@dataclass(frozen=True)
class Factor:
    fator: str
    valor: str
    efeito: Literal["aumenta o risco", "reduz o risco"]
    multiplicador: float  # fator sobre a taxa mensal de saída (ex.: 1.8 = 80% maior)


@dataclass(frozen=True)
class Verdict:
    conceder: bool
    motivo: str


@dataclass(frozen=True)
class RiskReport:
    resultado: Resultado
    empresa: CompanyInfo
    politica: dict[str, Any]
    prazo_pedido_meses: int | None
    veredito: Verdict | None
    prazo_maximo_aprovado_meses: int | None
    nivel_risco: str | None
    curva: list[RiskPoint] = field(default_factory=list)
    fatores: list[Factor] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    limitacoes: list[str] = field(default_factory=list)
    fonte: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RiskAssessor:
    def __init__(self, model: RiskModel, repository: CompanyRepository, policy: RiskPolicy) -> None:
        self.model = model
        self.repository = repository
        self.policy = policy
        self._validated = int(model.metadata.get("horizonte_maximo_validado_meses", 36))
        self._ranges: dict[str, dict[str, float]] = model.metadata.get("faixas_treino", {})

    def assess(self, cnpj_text: str, prazo_meses: int | None = None) -> RiskReport:
        """Relatório de risco para um CNPJ; com `prazo_meses`, inclui o veredito para esse prazo.

        Levanta `InvalidCNPJError` (o texto não é um CNPJ) ou `CompanyNotFoundError` (o CNPJ não
        existe na base).
        """
        if prazo_meses is not None and prazo_meses <= 0:
            raise ValueError("o prazo precisa ser um número positivo de meses")
        cnpj = parse_cnpj(cnpj_text)
        snapshot = self.repository.snapshot
        row = self.repository.get(cnpj.basico)
        if row is None:
            raise CompanyNotFoundError(cnpj, snapshot.data_referencia)

        empresa = _company_info(cnpj, row)
        politica = {
            "limite_risco_pct": round(100 * self.policy.limite_risco, 1),
            "prazo_maximo_meses": self.policy.prazo_maximo_meses,
            "regra": (
                f"conceder se a chance de o empregador sair até o fim do prazo for de no máximo "
                f"{pct(self.policy.limite_risco, 0)}"
            ),
        }
        fonte = {
            "data_referencia_dados": dt.date.fromisoformat(snapshot.data_referencia).strftime(
                "%d/%m/%Y"
            ),
            "fontes": [
                f"Receita Federal, CNPJ (foto {snapshot.foto_receita})",
                f"Receita Federal, regimes tributários (ECF {snapshot.ano_ecf})",
                f"PGFN, dívida ativa da União ({snapshot.trimestre_pgfn.replace('_', ' ')})",
            ],
            "modelo": self.model.metadata.get("modelo"),
        }
        avisos = _filial_warning(cnpj)

        situacao = int(row["situacao_cadastral"])
        if situacao != 2:
            return RiskReport(
                resultado="nao_elegivel",
                empresa=empresa,
                politica=politica,
                prazo_pedido_meses=prazo_meses,
                veredito=Verdict(
                    False,
                    f"a empresa está {SITUACOES.get(situacao, 'inativa')} na Receita "
                    f"desde {empresa.data_situacao_cadastral}: não há empregador ativo",
                ),
                prazo_maximo_aprovado_meses=None,
                nivel_risco=None,
                avisos=avisos,
                limitacoes=list(LIMITACOES),
                fonte=fonte,
            )
        if row["tem_situacao_especial"]:
            return RiskReport(
                resultado="nao_elegivel",
                empresa=empresa,
                politica=politica,
                prazo_pedido_meses=prazo_meses,
                veredito=Verdict(
                    False,
                    "a empresa tem situação especial registrada na Receita (por exemplo, "
                    "recuperação judicial ou falência)",
                ),
                prazo_maximo_aprovado_meses=None,
                nivel_risco=None,
                avisos=avisos,
                limitacoes=list(LIMITACOES),
                fonte=fonte,
            )
        out_of_scope = _out_of_scope_reason(row)
        if out_of_scope:
            return RiskReport(
                resultado="fora_do_escopo",
                empresa=empresa,
                politica=politica,
                prazo_pedido_meses=prazo_meses,
                veredito=None,
                prazo_maximo_aprovado_meses=None,
                nivel_risco=None,
                avisos=[*avisos, f"Fora do escopo do modelo: {out_of_scope}."],
                limitacoes=list(LIMITACOES),
                fonte=fonte,
            )

        firm = pd.DataFrame([{c: row[c] for c in SOURCE_COLUMNS}])
        terms = sorted(
            {*self.policy.prazos(), REFERENCE_HORIZON, *([prazo_meses] if prazo_meses else [])}
        )
        survival = self.model.survival(firm, tuple(terms))[0]
        risk = {t: float(1 - s) for t, s in zip(terms, survival, strict=True)}

        veredito = None
        if prazo_meses is not None:
            decision = decide_term(self.policy, prazo_meses, risk[prazo_meses])
            veredito = Verdict(decision.conceder, decision.motivo)
        maximo = max_term(self.policy, risk)
        explain_horizon = prazo_meses or maximo or REFERENCE_HORIZON

        return RiskReport(
            resultado="avaliada",
            empresa=empresa,
            politica=politica,
            prazo_pedido_meses=prazo_meses,
            veredito=veredito,
            prazo_maximo_aprovado_meses=maximo,
            nivel_risco=self.policy.nivel(risk[REFERENCE_HORIZON]),
            curva=[
                RiskPoint(
                    t,
                    round(100 * r, 1),
                    r <= self.policy.limite_risco,
                    t > self._validated,
                )
                for t, r in risk.items()
            ],
            fatores=self._factors(firm, row, explain_horizon),
            avisos=avisos + self._confidence_warnings(row, prazo_meses),
            limitacoes=list(LIMITACOES),
            fonte=fonte,
        )

    def _factors(self, firm: pd.DataFrame, row: dict[str, Any], horizon: int) -> list[Factor]:
        return [
            Factor(
                fator=d.label,
                valor=format_value(d.feature, row.get(d.feature), row),
                efeito="aumenta o risco" if d.contribution > 0 else "reduz o risco",
                multiplicador=round(math.exp(abs(d.contribution)), 2),
            )
            for d in self.model.drivers(firm, horizon=horizon, top=5)
        ]

    def _confidence_warnings(self, row: dict[str, Any], prazo_meses: int | None) -> list[str]:
        warnings = []
        if row["idade_meses"] is not None and row["idade_meses"] < MIN_RELIABLE_AGE_MONTHS:
            warnings.append(
                "Empresa com menos de 3 anos: o modelo distingue pouco entre empresas jovens, "
                "que ainda não acumularam sinais fiscais. Use a análise com cautela."
            )
        if prazo_meses is not None and prazo_meses > self._validated:
            warnings.append(
                f"Prazo acima de {self._validated} meses: o risco é extrapolado pelo modelo, "
                "sem validação com dados observados."
            )
        outside = [
            f"{format_label(name)} ({format_value(name, row.get(name), row)})"
            for name, bounds in self._ranges.items()
            if name in row
            and row[name] is not None
            and not _is_nan(row[name])
            and not (bounds["p001"] <= float(row[name]) <= bounds["p999"])
        ]
        if outside:
            warnings.append(
                "Valores fora da faixa vista no treino: "
                + "; ".join(outside)
                + ". O modelo trata esses casos como os extremos que conhece, sem extrapolar."
            )
        return warnings


def _filial_warning(cnpj: CNPJ) -> list[str]:
    if cnpj.is_matriz:
        return []
    return [
        f"O CNPJ informado é de uma filial (ordem {cnpj.ordem}). A análise é da empresa "
        f"(CNPJ básico {cnpj.basico}), que é quem mantém o vínculo dos funcionários."
    ]


def _out_of_scope_reason(row: dict[str, Any]) -> str | None:
    if row["opcao_mei"]:
        return "MEI pode ter no máximo um empregado e não faz parte do público do modelo"
    grupo = str(row["grupo_natureza"])
    if grupo in GRUPOS_FORA_DO_ESCOPO:
        return f"natureza jurídica de {GRUPOS_FORA_DO_ESCOPO[grupo]}"
    return None


def _is_nan(value: Any) -> bool:
    return isinstance(value, float) and math.isnan(value)


def _date(value: Any) -> str | None:
    return value.strftime("%d/%m/%Y") if isinstance(value, dt.date) else None


def _company_info(cnpj: CNPJ, row: dict[str, Any]) -> CompanyInfo:
    dividas = [
        nome
        for col, nome in (
            ("divida_fgts", "FGTS"),
            ("divida_previdenciaria", "previdenciária"),
            ("divida_nao_previdenciaria", "não previdenciária"),
        )
        if row.get(col)
    ]
    natureza = row.get("natureza_descricao") or row.get("natureza_juridica") or "não informada"
    atividade = row.get("cnae_descricao") or row.get("cnae_fiscal_principal") or "não informada"
    return CompanyInfo(
        cnpj=cnpj.formatted,
        cnpj_basico=cnpj.basico,
        razao_social=row.get("razao_social"),
        situacao_cadastral=SITUACOES.get(int(row["situacao_cadastral"]), "desconhecida"),
        data_situacao_cadastral=_date(row.get("data_situacao_cadastral")),
        data_inicio_atividade=_date(row.get("data_inicio_atividade")),
        idade=format_value("idade_meses", row.get("idade_meses"), row),
        porte=PORTES.get(str(row.get("porte")), "não informado"),
        natureza_juridica=str(natureza),
        atividade_principal=str(atividade),
        uf=row.get("uf"),
        regime_tributario=REGIMES.get(str(row.get("regime_tributario")), "não informado"),
        filiais_ativas=int(row.get("n_filiais_ativas") or 0),
        dividas_ativas_pgfn=dividas,
    )


def format_label(feature: str) -> str:
    return LABELS.get(feature, "idade da empresa" if feature == "idade_meses" else feature)


def _brl(value: float) -> str:
    text = f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {text}"


def format_value(feature: str, value: Any, row: dict[str, Any]) -> str:
    """Valor de uma feature em linguagem de negócio, para o relatório."""
    if value is None or _is_nan(value):
        if feature == "meses_desde_ultima_inscricao":
            return "sem dívida inscrita"
        return "não informado"
    if isinstance(value, bool):
        return "sim" if value else "não"
    if feature == "idade_meses":
        anos, meses = divmod(int(value), 12)
        partes = [f"{anos} ano{'s' if anos != 1 else ''}"] if anos else []
        if meses or not anos:
            partes.append(f"{meses} {'mês' if meses == 1 else 'meses'}")
        return " e ".join(partes)
    if feature in ("capital_social", "valor_divida"):
        return _brl(float(value))
    if feature == "regime_tributario":
        return REGIMES.get(str(value), str(value))
    if feature == "porte":
        return PORTES.get(str(value), str(value))
    if feature == "natureza_juridica":
        return str(row.get("natureza_descricao") or value)
    if feature == "cnae_divisao":
        return str(row.get("cnae_descricao") or f"divisão {value} da CNAE")
    if feature == "cnae_secao":
        return SECOES_CNAE.get(str(value), str(value))
    if feature == "grupo_natureza":
        return {"2": "entidade empresarial", "3": "entidade sem fins lucrativos"}.get(
            str(value), str(value)
        )
    if feature == "meses_desde_ultima_inscricao":
        return f"{int(value)} meses"
    if isinstance(value, int | float) and float(value).is_integer():
        return f"{int(value):,}".replace(",", ".")
    return str(value)
