"""Interface Streamlit. Não contém regra de negócio: só conversa com a API."""

import httpx
import streamlit as st

from consig_risk.config import get_settings

st.set_page_config(page_title="Risco de Consignado Privado", page_icon="📊")
st.title("Risco de Consignado Privado")

api_url = get_settings().api_url
try:
    response = httpx.get(f"{api_url}/health", timeout=5)
    response.raise_for_status()
    st.success(f"API disponível (versão {response.json()['version']}).")
except httpx.HTTPError as exc:
    st.error(f"Não foi possível falar com a API em {api_url}: {exc}")
