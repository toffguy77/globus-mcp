"""Общие фикстуры. Ни один тест не ходит в сеть и не трогает ~/.globus-mcp."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from globus_mcp import client as client_mod
from globus_mcp import server as server_mod
from globus_mcp.client import BFF, WEB, GlobusClient


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Состояние сессии — во временном каталоге, а не в домашнем."""
    monkeypatch.setenv(client_mod.STATE_HOME_ENV, str(tmp_path / "state-home"))
    yield tmp_path


@pytest.fixture(autouse=True)
def no_real_client(monkeypatch):
    """Сбрасывает синглтон между тестами, чтобы они не влияли друг на друга."""
    monkeypatch.setattr(server_mod, "_client", None, raising=False)
    yield
    monkeypatch.setattr(server_mod, "_client", None, raising=False)


class Recorder:
    """Ловит запросы и отдаёт заранее заготовленные ответы."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Any] = {}
        self.requests: list[httpx.Request] = []

    def route(self, method: str, url: str, payload: Any, status: int = 200) -> None:
        self.routes[(method.upper(), url)] = (payload, status)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, str(request.url).split("?")[0])
        if key not in self.routes:
            raise AssertionError(f"незамоканный запрос: {key}")
        payload, status = self.routes[key]
        if isinstance(payload, str):
            return httpx.Response(status, text=payload)
        return httpx.Response(status, json=payload)

    def body(self, index: int = 0) -> Any:
        return json.loads(self.requests[index].content)


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def fake_client(recorder, monkeypatch) -> GlobusClient:
    """Клиент с подменённым транспортом, подставленный в синглтон сервера."""
    gc = GlobusClient()
    gc._http = httpx.Client(transport=httpx.MockTransport(recorder.handler))
    monkeypatch.setattr(server_mod, "_client", gc)
    return gc


def bff(path: str) -> str:
    return f"{BFF}/{path}"


def web(path: str) -> str:
    return f"{WEB}{path}"
