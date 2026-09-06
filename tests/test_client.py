"""Транспорт, состояние и распаковка ответов."""

from __future__ import annotations

import base64
import json
import os
import stat
import sys

import httpx
import pytest

from globus_mcp.client import GlobusClient, GlobusError, state_dir


def make_client(handler) -> GlobusClient:
    gc = GlobusClient()
    gc._http = httpx.Client(transport=httpx.MockTransport(handler))
    return gc


# ------------------------------------------------------------------- состояние


def test_state_dir_respects_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GLOBUS_MCP_HOME", str(tmp_path / "custom"))
    assert state_dir() == tmp_path / "custom"


def test_state_file_created_with_app_id():
    gc = GlobusClient()
    assert gc.app_id
    assert gc.state_file.exists()
    assert json.loads(gc.state_file.read_text())["app_id"] == gc.app_id


@pytest.mark.skipif(sys.platform == "win32", reason="права POSIX")
def test_state_file_is_private():
    """В файле лежит JWT — он не должен быть читаем группой и остальными."""
    gc = GlobusClient()
    mode = stat.S_IMODE(os.stat(gc.state_file).st_mode)
    assert mode == 0o600


def test_state_survives_reload():
    first = GlobusClient()
    first.set_token("abc")
    second = GlobusClient()
    assert second.app_id == first.app_id
    assert second.token == "abc"


def test_broken_state_file_is_regenerated():
    gc = GlobusClient()
    gc.state_file.write_text("не json")
    fresh = GlobusClient()
    assert fresh.app_id


def test_token_roundtrip_and_logout():
    gc = GlobusClient()
    gc.set_token("t")
    assert gc.token == "t"
    gc.set_token(None)
    assert gc.token is None


def test_phone_property_persists():
    gc = GlobusClient()
    gc.phone = "79161234567"
    assert GlobusClient().phone == "79161234567"


# ------------------------------------------------------------------------ jwt


def _jwt(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{body}.signature"


def test_token_payload_decodes():
    gc = GlobusClient()
    gc.set_token(_jwt({"phone_number": "79161234567", "is_registered": True}))
    assert gc.token_payload()["phone_number"] == "79161234567"


@pytest.mark.parametrize("token", [None, "", "не-jwt", "a.b", "a.!!!.c"])
def test_token_payload_tolerates_garbage(token):
    gc = GlobusClient()
    gc.set_token(token)
    assert gc.token_payload() is None


# -------------------------------------------------------------------- заголовки


def test_app_id_header_always_sent_token_only_when_present():
    seen = []

    def handler(request):
        seen.append(request.headers)
        return httpx.Response(200, json={"data": {}})

    gc = make_client(handler)
    gc.get("context")
    assert seen[0]["x-app-id"] == gc.app_id
    assert "authorization" not in seen[0]

    gc.set_token("jwt")
    gc.get("context")
    assert seen[1]["authorization"] == "Bearer jwt"


# ------------------------------------------------------------------ распаковка


def test_unwrap_returns_data_envelope():
    gc = make_client(lambda r: httpx.Response(200, json={"data": {"ok": 1}}))
    assert gc.get("context") == {"ok": 1}


def test_errors_raise_even_on_http_200():
    """BFF отдаёт 200 с непустым errors — это всё равно ошибка."""
    gc = make_client(
        lambda r: httpx.Response(200, json={"data": None, "errors": [{"message": "нет доступа"}]})
    )
    with pytest.raises(GlobusError, match="нет доступа"):
        gc.get("checkout")


def test_http_error_without_errors_raises():
    gc = make_client(lambda r: httpx.Response(401, json={"data": None}))
    with pytest.raises(GlobusError, match="401"):
        gc.get("checkout")


def test_non_json_response_raises():
    gc = make_client(lambda r: httpx.Response(502, text="<html>bad gateway</html>"))
    with pytest.raises(GlobusError, match="502"):
        gc.get("basket")


# --------------------------------------------------------------------- ssr next


def test_build_id_is_scraped_and_cached():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text='<script>{"buildId":"XYZ","x":1}</script>')

    gc = make_client(handler)
    assert gc.build_id() == "XYZ"
    assert gc.build_id() == "XYZ"
    assert len(calls) == 1


def test_build_id_missing_raises():
    gc = make_client(lambda r: httpx.Response(200, text="<html></html>"))
    with pytest.raises(GlobusError, match="buildId"):
        gc.build_id()


def test_next_data_refetches_build_id_on_404():
    """buildId протухает после деплоя сайта: первый 404 — перечитать и повторить."""
    seq = []

    def handler(request):
        url = str(request.url)
        seq.append(url)
        if url.endswith("/"):
            return httpx.Response(200, text=f'{{"buildId":"B{len(seq)}"}}')
        if "B1" in url:
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"pageProps": {"ok": True}})

    gc = make_client(handler)
    assert gc.next_data("/products/x") == {"pageProps": {"ok": True}}
    assert sum("_next/data" in u for u in seq) == 2


def test_query_data_finds_by_key_fragment():
    payload = {
        "pageProps": {
            "dehydratedState": {
                "queries": [
                    {"queryKey": ["other"], "state": {"data": "нет"}},
                    {"queryKey": ["product-detail", "42"], "state": {"data": "да"}},
                ]
            }
        }
    }
    assert GlobusClient.query_data(payload, "product-detail") == "да"
    assert GlobusClient.query_data(payload, "отсутствует") is None
    assert GlobusClient.query_data({}, "x") is None


def test_client_closes_as_context_manager():
    with GlobusClient() as gc:
        assert gc.app_id
    assert gc._http.is_closed
