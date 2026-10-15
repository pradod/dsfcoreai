from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from consig_risk.data.remote import RemoteFile, download, parse_propfind
from consig_risk.data.sources import receita_files

PROPFIND_XML = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/public.php/webdav/2026-10/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/public.php/webdav/2026-10/Empresas1.zip</d:href>
    <d:propstat><d:prop><d:getcontentlength>77901368</d:getcontentlength></d:prop></d:propstat>
  </d:response>
  <d:response>
    <d:href>/public.php/webdav/2026-10/Simples%20novo.zip</d:href>
    <d:propstat><d:prop><d:getcontentlength>10</d:getcontentlength></d:prop></d:propstat>
  </d:response>
</d:multistatus>"""


def test_parse_propfind_skips_own_folder_and_decodes_names() -> None:
    files = parse_propfind(PROPFIND_XML, webdav_prefix="/public.php/webdav/", folder="2026-10")

    assert files == [
        RemoteFile(path="2026-10/Empresas1.zip", size=77901368),
        RemoteFile(path="2026-10/Simples novo.zip", size=10),
    ]
    assert files[0].name == "Empresas1.zip"


def test_receita_files_expands_partitioned_tables() -> None:
    names = receita_files(("Empresas", "Simples"))

    assert names[:2] == ["Empresas0.zip", "Empresas1.zip"]
    assert len(names) == 11
    assert names[-1] == "Simples.zip"


def test_receita_files_rejects_unknown_table() -> None:
    with pytest.raises(ValueError, match="desconhecida"):
        receita_files(("Inexistente",))


PAYLOAD = b"0123456789" * 1000


def _broken_stream(body: bytes, fail_after: int) -> Iterator[bytes]:
    yield body[:fail_after]
    raise httpx.ReadError("conexão interrompida")


def _range_server(fail_after: int | None = None) -> httpx.MockTransport:
    """Servidor falso que respeita Range e pode cortar a conexão depois de N bytes."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        start = 0
        if range_header := request.headers.get("Range"):
            start = int(range_header.removeprefix("bytes=").rstrip("-"))
        body = PAYLOAD[start:]
        status = 206 if start else 200
        if fail_after is not None and calls["n"] == 1:
            return httpx.Response(status, content=_broken_stream(body, fail_after))
        return httpx.Response(status, content=body)

    return httpx.MockTransport(handler)


def test_download_writes_file_and_is_idempotent(tmp_path: Path) -> None:
    dest = tmp_path / "f.zip"
    with httpx.Client(transport=_range_server()) as client:
        download(client, "https://x/f.zip", dest, expected_size=len(PAYLOAD))
        mtime = dest.stat().st_mtime_ns
        download(client, "https://x/f.zip", dest, expected_size=len(PAYLOAD))

    assert dest.read_bytes() == PAYLOAD
    assert dest.stat().st_mtime_ns == mtime  # segunda chamada não baixou de novo
    assert not (tmp_path / "f.zip.part").exists()


def test_download_resumes_after_connection_drop(tmp_path: Path) -> None:
    dest = tmp_path / "f.zip"
    with httpx.Client(transport=_range_server(fail_after=3000)) as client:
        download(client, "https://x/f.zip", dest, expected_size=len(PAYLOAD), backoff_seconds=0)

    assert dest.read_bytes() == PAYLOAD


def test_download_rejects_size_mismatch(tmp_path: Path) -> None:
    dest = tmp_path / "f.zip"
    with (
        httpx.Client(transport=_range_server()) as client,
        pytest.raises(OSError, match="tamanho"),
    ):
        download(client, "https://x/f.zip", dest, expected_size=len(PAYLOAD) + 1, retries=1)

    assert not dest.exists()
