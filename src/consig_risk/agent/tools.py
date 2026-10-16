"""Ferramentas do agent. Elas são a única fonte de números da conversa.

Cada ferramenta devolve um dicionário com `status` igual a "ok" ou "erro". Erros esperados (CNPJ
inválido, inexistente, prazo inválido) viram mensagens para o analista em vez de exceções, para o
agent poder explicar o problema sem inventar uma resposta.
"""

from collections.abc import Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from consig_risk.risk.assessment import CompanyNotFoundError, RiskAssessor
from consig_risk.risk.cnpj import InvalidCNPJError
from consig_risk.risk.policy import pct

# Chave do estado da sessão com o CNPJ da empresa em análise (permite perguntas de
# acompanhamento sem repetir o CNPJ).
STATE_CNPJ = "cnpj_em_analise"


def _error(message: str) -> dict[str, Any]:
    return {"status": "erro", "mensagem": message}


def make_tools(assessor: RiskAssessor) -> list[Callable[..., dict[str, Any]]]:
    def analisar_risco(
        tool_context: ToolContext,
        cnpj: str | None = None,
        prazo_meses: int | None = None,
    ) -> dict[str, Any]:
        """Analisa o risco de o empregador sair da situação ativa antes do fim do consignado.

        Args:
            cnpj: CNPJ da empresa, com ou sem pontuação. Omita para continuar analisando a
                empresa da conversa.
            prazo_meses: prazo do consignado em meses. Omita para obter o prazo máximo aprovado.

        Returns:
            Relatório com empresa, veredito (se houver prazo), prazo máximo aprovado, nível de
            risco, curva de risco por prazo, fatores que mais pesam, avisos e limitações.
        """
        cnpj = cnpj or tool_context.state.get(STATE_CNPJ)
        if not cnpj:
            return _error(
                "Nenhuma empresa em análise nesta conversa. Peça ao analista o CNPJ da empresa."
            )
        try:
            report = assessor.assess(cnpj, prazo_meses)
        except InvalidCNPJError as exc:
            return _error(f"CNPJ inválido: {exc}. Confira os 14 caracteres do CNPJ.")
        except CompanyNotFoundError as exc:
            return _error(
                f"Não foi possível analisar: {exc}. Confira o CNPJ; empresas abertas depois da "
                "data de referência ainda não estão na base."
            )
        except ValueError as exc:
            return _error(f"Pedido inválido: {exc}.")
        tool_context.state[STATE_CNPJ] = report.empresa.cnpj
        return {"status": "ok", **report.to_dict()}

    def explicar_politica() -> dict[str, Any]:
        """Explica o critério de risco aceitável, os dados usados e a qualidade do modelo.

        Returns:
            Regra da política, limites, motivos das escolhas, fontes de dados e métricas do modelo.
        """
        policy = assessor.policy
        meta = assessor.model.metadata
        validation = meta.get("validacao", {}).get("por_horizonte", [])
        snapshot = assessor.repository.snapshot
        return {
            "status": "ok",
            "regra": (
                "Conceder um consignado de N meses se a chance de o empregador sair da situação "
                "cadastral ativa até o fim do prazo for de no máximo "
                f"{pct(policy.limite_risco, 0)}."
            ),
            "limite_risco_pct": round(100 * policy.limite_risco, 1),
            "prazo_maximo_meses": policy.prazo_maximo_meses,
            "por_que_esse_limite": (
                f"Na simulação com empresas que o modelo não viu, o limite de "
                f"{pct(policy.limite_risco, 0)} aprova cerca de metade dos empregadores para 24 "
                "meses; entre os aprovados, a saída real fica cerca de 2,5 vezes abaixo da média, "
                "e cerca de 80% das empresas que fecharam teriam sido barradas."
            ),
            "por_que_esse_prazo_maximo": (
                f"{policy.prazo_maximo_meses} meses é o horizonte em que o modelo foi validado com "
                "dados observados; acima disso o risco seria extrapolado."
            ),
            "o_que_e_o_evento": (
                "O empregador sair da situação ativa na Receita (baixa, inaptidão por omissão de "
                "declarações, suspensão ou nulidade). Isso interrompe o desconto em folha."
            ),
            "niveis_de_risco_em_12_meses": {
                "baixo": f"até {pct(policy.faixa_baixo, 0)}",
                "moderado": f"até {pct(policy.faixa_moderado, 0)}",
                "alto": f"até {pct(policy.faixa_alto, 0)}",
                "muito alto": f"acima de {pct(policy.faixa_alto, 0)}",
            },
            "dados": {
                "data_referencia": snapshot.data_referencia,
                "fontes": [
                    "Receita Federal: cadastro de CNPJ "
                    "(empresas, estabelecimentos, Simples e sócios)",
                    f"Receita Federal: regimes tributários da ECF ({snapshot.ano_ecf})",
                    "PGFN: dívida ativa da União (FGTS, previdenciária e não previdenciária)",
                ],
            },
            "modelo": {
                "tipo": meta.get("modelo"),
                "empresas_no_treino": meta.get("empresas_treino"),
                "auc_por_horizonte_meses": {
                    int(m["horizonte"]): round(float(m["auc"]), 2) for m in validation
                },
            },
        }

    return [analisar_risco, explicar_politica]
