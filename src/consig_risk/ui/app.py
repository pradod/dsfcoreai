"""Interface Streamlit. Não contém regra de negócio: só conversa com a API.

Mostra a conversa com o agent e, abaixo de cada resposta, o relatório estruturado que a
ferramenta devolveu (curva de risco, fatores, avisos) e a checagem de que os números do texto
vêm dos dados.
"""

from typing import Any

import altair as alt
import httpx
import pandas as pd
import streamlit as st

from consig_risk.config import get_settings

API = get_settings().api_url
# Uma resposta do agent faz duas chamadas ao LLM; em provedores lentos (ex.: endpoints gratuitos
# com modelos de raciocínio) isso passa de dois minutos.
TIMEOUT = httpx.Timeout(300.0, connect=5.0)
EXEMPLOS = (
    "Analise o CNPJ 00.000.000/0001-91 para um consignado de 24 meses.",
    "Faça o relatório de risco do CNPJ 00.000.000/0001-91.",
    "E se fossem 12 meses?",
    "Por que esse limite de risco?",
)

st.set_page_config(page_title="Risco de Consignado Privado", page_icon="📊", layout="wide")


def api(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    """Chama a API e converte erros em mensagens legíveis (levanta RuntimeError)."""
    try:
        response = httpx.request(method, f"{API}{path}", timeout=TIMEOUT, **kwargs)
    except httpx.TimeoutException as exc:
        raise RuntimeError("A API demorou demais para responder. Tente novamente.") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Não foi possível falar com a API em {API}.") from exc
    if response.is_error:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise RuntimeError(detail if isinstance(detail, str) else "Pedido inválido.")
    data: dict[str, Any] = response.json()
    return data


def render_report(report: dict[str, Any], numeros_nao_verificados: list[str] | None) -> None:
    """Relatório estruturado; com `numeros_nao_verificados`, mostra a checagem do texto do agent."""
    if numeros_nao_verificados:
        st.warning(
            "Atenção: a resposta cita números que não aparecem nos dados das ferramentas: "
            + ", ".join(numeros_nao_verificados)
            + ". Confira no relatório abaixo antes de usar."
        )
    elif numeros_nao_verificados is not None:
        st.caption("✓ Todos os números da resposta conferem com os dados das ferramentas.")
    if not report:
        return
    with st.expander("Relatório estruturado (dados da ferramenta)", expanded=False):
        curva = report.get("curva") or []
        if curva:
            df = pd.DataFrame(curva)
            limite = report["politica"]["limite_risco_pct"]
            base = alt.Chart(df).encode(
                x=alt.X("prazo_meses:O", title="prazo (meses)"),
                y=alt.Y("risco_pct:Q", title="risco de o empregador sair (%)"),
                tooltip=[
                    alt.Tooltip("prazo_meses:O", title="prazo (meses)"),
                    alt.Tooltip("risco_pct:Q", title="risco (%)", format=".1f"),
                ],
            )
            line = base.mark_line(point=alt.OverlayMarkDef(size=60))
            rule = (
                alt.Chart(pd.DataFrame({"limite": [limite]}))
                .mark_rule(strokeDash=[4, 4])
                .encode(y="limite:Q", tooltip=alt.Tooltip("limite:Q", title="limite (%)"))
            )
            st.altair_chart(line + rule, use_container_width=True)
            st.caption(f"Linha tracejada: limite da política ({limite:.0f}%).")
        cols = st.columns(2)
        with cols[0]:
            st.markdown("**Empresa**")
            st.json(report.get("empresa", {}), expanded=False)
        with cols[1]:
            if report.get("fatores"):
                st.markdown("**O que mais pesa**")
                st.dataframe(pd.DataFrame(report["fatores"]), hide_index=True)
        for aviso in report.get("avisos", []):
            st.info(aviso)


# --- Estado e barra lateral --------------------------------------------------------------

if "mensagens" not in st.session_state:
    st.session_state.mensagens = []  # [{papel, texto, relatorio, nao_verificados}]
    st.session_state.conversa_id = None

try:
    health = api("GET", "/health")
except RuntimeError as exc:
    st.error(f"{exc} Verifique se o serviço `api` está rodando.")
    st.stop()

with st.sidebar:
    st.header("Sistema")
    st.write(f"Dados da Receita de **{health['dados']['data_referencia']}**")
    st.write(f"Modelo: {health['modelo']}")
    if health["llm"]["configurado"]:
        st.write(f"LLM: `{health['llm']['modelo']}`")
    if st.button("Nova conversa", use_container_width=True):
        st.session_state.mensagens = []
        st.session_state.conversa_id = None
        st.rerun()
    st.divider()
    st.caption("Exemplos de pedido:")
    for exemplo in EXEMPLOS:
        st.caption(f"• {exemplo}")

st.title("Risco de Consignado Privado")
st.caption(
    "Informe o CNPJ do empregador. O agent calcula a chance de a empresa deixar de existir antes "
    "do fim do crédito, com base em dados públicos e num modelo treinado com 1,9 milhão de "
    "empresas. A decisão final é do analista."
)

# --- Sem LLM: consulta direta -------------------------------------------------------------

if not health["llm"]["configurado"]:
    st.warning(
        "O agent está indisponível porque o LLM não foi configurado. Preencha LLM_MODEL e "
        "LLM_API_KEY no arquivo .env e reinicie com `docker compose up`. Enquanto isso, a "
        "consulta direta abaixo usa o mesmo modelo, sem conversa."
    )
    with st.form("consulta"):
        cnpj = st.text_input("CNPJ", placeholder="00.000.000/0001-91")
        prazo = st.number_input("Prazo (meses, 0 = prazo em aberto)", 0, 96, 24, step=6)
        enviado = st.form_submit_button("Analisar")
    if enviado and cnpj:
        with st.spinner("Calculando o risco..."):
            try:
                params = {"prazo_meses": int(prazo)} if prazo else {}
                st.session_state.relatorio_direto = api(
                    "GET", f"/empresas/{cnpj.strip()}/risco", params=params
                )
            except RuntimeError as exc:
                st.session_state.relatorio_direto = None
                st.error(str(exc))
    relatorio = st.session_state.get("relatorio_direto")
    if relatorio:
        if veredito := relatorio.get("veredito"):
            st.subheader("Conceder" if veredito["conceder"] else "Não conceder")
            st.write(veredito["motivo"])
        if relatorio["resultado"] == "avaliada":
            maximo = relatorio.get("prazo_maximo_aprovado_meses")
            st.write(
                f"Prazo máximo aprovado: {maximo} meses"
                if maximo
                else "Não conceder em nenhum prazo da política."
            )
        render_report(relatorio, None)
    st.stop()

# --- Conversa -----------------------------------------------------------------------------

for mensagem in st.session_state.mensagens:
    with st.chat_message("user" if mensagem["papel"] == "analista" else "assistant"):
        st.markdown(mensagem["texto"])
        if mensagem["papel"] == "agent":
            render_report(mensagem.get("relatorio") or {}, mensagem.get("nao_verificados", []))

if pergunta := st.chat_input("Ex.: Analise o CNPJ 00.000.000/0001-91 para 24 meses"):
    st.session_state.mensagens.append({"papel": "analista", "texto": pergunta})
    with st.chat_message("user"):
        st.markdown(pergunta)
    with (
        st.chat_message("assistant"),
        st.spinner("Analisando os dados da empresa (pode levar alguns minutos, conforme o LLM)..."),
    ):
        try:
            if st.session_state.conversa_id is None:
                st.session_state.conversa_id = api("POST", "/conversas")["conversa_id"]
            resposta = api(
                "POST",
                f"/conversas/{st.session_state.conversa_id}/mensagens",
                json={"texto": pergunta},
            )
        except RuntimeError as exc:
            st.error(str(exc))
            st.session_state.mensagens.pop()  # permite reenviar a mesma pergunta
        else:
            st.markdown(resposta["texto"])
            render_report(resposta.get("relatorio") or {}, resposta["numeros_nao_verificados"])
            st.session_state.mensagens.append(
                {
                    "papel": "agent",
                    "texto": resposta["texto"],
                    "relatorio": resposta.get("relatorio"),
                    "nao_verificados": resposta["numeros_nao_verificados"],
                }
            )
