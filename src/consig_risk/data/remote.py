"""Acesso às fontes remotas: listagem de arquivos e download que retoma de onde parou.

Os servidores públicos (Receita e PGFN) são instáveis e os arquivos têm centenas de MB, então
todo download grava num `.part`, retoma com `Range` depois de uma falha e confere o tamanho final.
"""

import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import httpx

logger = logging.getLogger(__name__)

_DAV_NS = {"d": "DAV:"}
_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class RemoteFile:
    """Um arquivo remoto, identificado pelo caminho relativo à raiz da fonte."""

    path: str
    size: int | None = None

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


class NextcloudShare:
    """Compartilhamento público do Nextcloud da Receita (dados.gov.br aponta para ele).

    O compartilhamento é acessado por WebDAV, usando o token do link público como usuário.
    """

    def __init__(self, base_url: str, token: str, client: httpx.Client) -> None:
        self._webdav_root = f"{base_url.rstrip('/')}/public.php/webdav"
        self._auth = httpx.BasicAuth(token, "")
        self._client = client

    def list(self, folder: str = "") -> list[RemoteFile]:
        """Lista o conteúdo direto de `folder` (sem recursão), sem incluir a própria pasta."""
        url = f"{self._webdav_root}/{folder.strip('/')}/" if folder else f"{self._webdav_root}/"
        response = self._client.request("PROPFIND", url, auth=self._auth, headers={"Depth": "1"})
        response.raise_for_status()
        return parse_propfind(response.text, webdav_prefix="/public.php/webdav/", folder=folder)

    def url_for(self, remote: RemoteFile) -> str:
        return f"{self._webdav_root}/{remote.path}"

    @property
    def auth(self) -> httpx.Auth:
        return self._auth


def parse_propfind(xml_text: str, webdav_prefix: str, folder: str = "") -> list[RemoteFile]:
    """Converte a resposta multistatus de um PROPFIND em `RemoteFile`.

    Pastas viram entradas com `size=None` e caminho terminado em `/`.
    """
    root = ET.fromstring(xml_text)
    own_path = folder.strip("/")
    files: list[RemoteFile] = []
    for response in root.findall("d:response", _DAV_NS):
        href = unquote(response.findtext("d:href", default="", namespaces=_DAV_NS))
        path = href.split(webdav_prefix, 1)[-1]
        if path.strip("/") == own_path:
            continue
        size_text = response.findtext(".//d:getcontentlength", namespaces=_DAV_NS)
        files.append(RemoteFile(path=path, size=int(size_text) if size_text else None))
    return files


def download(
    client: httpx.Client,
    url: str,
    dest: Path,
    *,
    expected_size: int | None = None,
    auth: httpx.Auth | None = None,
    retries: int = 5,
    backoff_seconds: float = 5.0,
) -> Path:
    """Baixa `url` em `dest`, retomando downloads parciais. Idempotente.

    Se `dest` já existe com o tamanho esperado, não baixa de novo.
    """
    if dest.exists() and (expected_size is None or dest.stat().st_size == expected_size):
        logger.info("já baixado: %s", dest.name)
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")

    for attempt in range(1, retries + 1):
        try:
            _download_once(client, url, partial, auth)
            break
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            if attempt == retries:
                raise
            wait = backoff_seconds * attempt
            logger.warning(
                "falha ao baixar %s (%s); nova tentativa %d/%d em %.0fs",
                dest.name,
                exc,
                attempt + 1,
                retries,
                wait,
            )
            time.sleep(wait)

    actual_size = partial.stat().st_size
    if expected_size is not None and actual_size != expected_size:
        partial.unlink()
        raise OSError(
            f"{dest.name}: tamanho baixado ({actual_size}) difere do esperado ({expected_size})"
        )
    partial.replace(dest)
    return dest


def _download_once(client: httpx.Client, url: str, partial: Path, auth: httpx.Auth | None) -> None:
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    with client.stream("GET", url, headers=headers, auth=auth) as response:
        if response.status_code == 416:
            # O .part já tem o arquivo inteiro.
            return
        response.raise_for_status()
        resumed = response.status_code == 206
        if offset and not resumed:
            logger.info("servidor não aceitou retomar %s; baixando do início", partial.name)
        with partial.open("ab" if resumed else "wb") as fh:
            for chunk in response.iter_bytes(_CHUNK_SIZE):
                fh.write(chunk)
