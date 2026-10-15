"""Layout das tabelas da Receita (dicionário oficial "cnpj-metadados.pdf") e o que guardamos.

Os CSVs não têm cabeçalho, então os nomes vêm daqui. Só guardamos o que o modelo e o agent usam:
telefones, e-mails, endereços e nomes/CPFs de sócios ficam de fora (minimização de dados, LGPD).
"""

from dataclasses import dataclass


def _date(col: str) -> str:
    # Datas vêm como AAAAMMDD; "0" e "00000000" significam ausente.
    return f"try_strptime(nullif({col}, ''), '%Y%m%d')::DATE"


@dataclass(frozen=True)
class TableSpec:
    name: str
    raw_columns: tuple[str, ...]
    # coluna de saída -> expressão SQL sobre as colunas brutas (todas lidas como VARCHAR)
    select: dict[str, str]

    def select_sql(self) -> str:
        return ",\n  ".join(f"{expr} AS {out}" for out, expr in self.select.items())


EMPRESAS = TableSpec(
    name="Empresas",
    raw_columns=(
        "cnpj_basico",
        "razao_social",
        "natureza_juridica",
        "qualificacao_responsavel",
        "capital_social",
        "porte",
        "ente_federativo",
    ),
    select={
        "cnpj_basico": "cnpj_basico",
        "razao_social": "razao_social",
        "natureza_juridica": "natureza_juridica",
        "qualificacao_responsavel": "qualificacao_responsavel",
        "capital_social": "try_cast(replace(capital_social, ',', '.') AS DOUBLE)",
        "porte": "nullif(porte, '')",
        "ente_federativo": "nullif(ente_federativo, '')",
    },
)

ESTABELECIMENTOS = TableSpec(
    name="Estabelecimentos",
    raw_columns=(
        "cnpj_basico",
        "cnpj_ordem",
        "cnpj_dv",
        "identificador_matriz_filial",
        "nome_fantasia",
        "situacao_cadastral",
        "data_situacao_cadastral",
        "motivo_situacao_cadastral",
        "nome_cidade_exterior",
        "pais",
        "data_inicio_atividade",
        "cnae_fiscal_principal",
        "cnae_fiscal_secundaria",
        "tipo_logradouro",
        "logradouro",
        "numero",
        "complemento",
        "bairro",
        "cep",
        "uf",
        "municipio",
        "ddd_1",
        "telefone_1",
        "ddd_2",
        "telefone_2",
        "ddd_fax",
        "fax",
        "correio_eletronico",
        "situacao_especial",
        "data_situacao_especial",
    ),
    select={
        "cnpj_basico": "cnpj_basico",
        "cnpj_ordem": "cnpj_ordem",
        "cnpj_dv": "cnpj_dv",
        "matriz": "identificador_matriz_filial = '1'",
        "nome_fantasia": "nullif(nome_fantasia, '')",
        "situacao_cadastral": "try_cast(situacao_cadastral AS TINYINT)",
        "data_situacao_cadastral": _date("data_situacao_cadastral"),
        "motivo_situacao_cadastral": "nullif(motivo_situacao_cadastral, '')",
        "pais": "nullif(pais, '')",
        "data_inicio_atividade": _date("data_inicio_atividade"),
        "cnae_fiscal_principal": "cnae_fiscal_principal",
        "n_cnaes_secundarios": (
            "CASE WHEN coalesce(cnae_fiscal_secundaria, '') = '' THEN 0 "
            "ELSE len(string_split(cnae_fiscal_secundaria, ',')) END"
        ),
        "uf": "uf",
        "municipio": "municipio",
        "situacao_especial": "nullif(trim(situacao_especial), '')",
        "data_situacao_especial": _date("data_situacao_especial"),
    },
)

SIMPLES = TableSpec(
    name="Simples",
    raw_columns=(
        "cnpj_basico",
        "opcao_simples",
        "data_opcao_simples",
        "data_exclusao_simples",
        "opcao_mei",
        "data_opcao_mei",
        "data_exclusao_mei",
    ),
    select={
        "cnpj_basico": "cnpj_basico",
        "opcao_simples": "opcao_simples = 'S'",
        "data_opcao_simples": _date("data_opcao_simples"),
        "data_exclusao_simples": _date("data_exclusao_simples"),
        "opcao_mei": "opcao_mei = 'S'",
        "data_opcao_mei": _date("data_opcao_mei"),
        "data_exclusao_mei": _date("data_exclusao_mei"),
    },
)

SOCIOS = TableSpec(
    name="Socios",
    raw_columns=(
        "cnpj_basico",
        "identificador_socio",
        "nome_socio",
        "cnpj_cpf_socio",
        "qualificacao_socio",
        "data_entrada_sociedade",
        "pais",
        "representante_legal",
        "nome_representante",
        "qualificacao_representante",
        "faixa_etaria",
    ),
    # Nomes, CPFs e representantes ficam de fora: não são usados e são dados pessoais.
    select={
        "cnpj_basico": "cnpj_basico",
        "identificador_socio": "try_cast(identificador_socio AS TINYINT)",
        "qualificacao_socio": "qualificacao_socio",
        "data_entrada_sociedade": _date("data_entrada_sociedade"),
        "socio_estrangeiro": "coalesce(pais, '') NOT IN ('', '105')",
        "faixa_etaria": "try_cast(faixa_etaria AS TINYINT)",
    },
)


def _lookup(name: str) -> TableSpec:
    return TableSpec(
        name=name,
        raw_columns=("codigo", "descricao"),
        select={"codigo": "codigo", "descricao": "descricao"},
    )


TABLES: dict[str, TableSpec] = {
    spec.name: spec
    for spec in (
        EMPRESAS,
        ESTABELECIMENTOS,
        SIMPLES,
        SOCIOS,
        *(
            _lookup(n)
            for n in ("Cnaes", "Motivos", "Municipios", "Naturezas", "Paises", "Qualificacoes")
        ),
    )
}
