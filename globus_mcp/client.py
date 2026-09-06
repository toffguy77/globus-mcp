"""HTTP-клиент к внутреннему API online.globus.ru (d1-front-bff).

Разведанные факты:
  * online.globus.ru → www.globus.ru (Next.js SSR)
  * BFF: https://digitalone.globus.ru/d1-front-bff/api-web/v1
  * Заголовки: X-App-Id: <uuid устройства> (обязателен),
    Authorization: Bearer <JWT> (опционален — без него сессия анонимная,
    корзина/контекст привязаны к device_id).
  * Часть данных (детальная карточка товара) отдаётся только SSR-ом,
    поэтому берётся из /_next/data/<buildId>/... .
  * Цены во всех ответах — в копейках (12999 == 129.99 ₽).

Подробности разведки — docs/API.md.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import uuid
from pathlib import Path
from types import TracebackType
from typing import Any

import httpx

WEB = "https://www.globus.ru"
BFF = "https://digitalone.globus.ru/d1-front-bff/api-web/v1"

#: Переменная окружения, переопределяющая каталог состояния (удобно для тестов
#: и для нескольких независимых сессий на одной машине).
STATE_HOME_ENV = "GLOBUS_MCP_HOME"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

# purchase_method
PM_PICKUP_STORE = 1   # самовывоз из гипермаркета (нужен store_id)
PM_DELIVERY = 2       # доставка по адресу (нужен address_id)
PM_PVZ = 3            # пункт выдачи (нужен pvz_id)


def state_dir() -> Path:
    """Каталог состояния: $GLOBUS_MCP_HOME или ~/.globus-mcp."""
    override = os.environ.get(STATE_HOME_ENV)
    return Path(override).expanduser() if override else Path.home() / ".globus-mcp"


class GlobusError(RuntimeError):
    """Ошибка обращения к API ГЛОБУС."""


class GlobusClient:
    """Транспорт и состояние сессии. Форматированием ответов не занимается."""

    def __init__(self, timeout: float = 30.0, state_file: Path | None = None) -> None:
        self._state_file = state_file or (state_dir() / "state.json")
        self._state = self._load_state()
        self._http = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"},
        )
        self._build_id: str | None = None
        self._build_id_ts: float = 0.0

    # ------------------------------------------------------------------ state

    def _load_state(self) -> dict[str, Any]:
        if self._state_file.exists():
            try:
                state = json.loads(self._state_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                state = {}
            if isinstance(state, dict) and state.get("app_id"):
                return state
        state = {"app_id": str(uuid.uuid4()), "token": None, "phone": None}
        self._write_state(state)
        return state

    def _write_state(self, state: dict[str, Any]) -> None:
        """Атомарная запись с правами 600 — в файле лежит JWT."""
        self._state_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self._state_file.with_name(self._state_file.name + f".{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self._state_file)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def save(self) -> None:
        self._write_state(self._state)

    @property
    def state_file(self) -> Path:
        return self._state_file

    @property
    def app_id(self) -> str:
        return self._state["app_id"]

    @property
    def token(self) -> str | None:
        return self._state.get("token") or None

    def set_token(self, token: str | None) -> None:
        self._state["token"] = token
        self.save()

    @property
    def phone(self) -> str | None:
        """Телефон последнего запроса SMS-кода — нужен на втором шаге входа."""
        return self._state.get("phone") or None

    @phone.setter
    def phone(self, value: str | None) -> None:
        self._state["phone"] = value
        self.save()

    def token_payload(self) -> dict[str, Any] | None:
        """Payload JWT без проверки подписи — только чтобы показать, кто вошёл."""
        tok = self.token
        if not tok or tok.count(".") != 2:
            return None
        raw = tok.split(".")[1]
        raw += "=" * (-len(raw) % 4)
        try:
            payload = json.loads(base64.urlsafe_b64decode(raw))
        except (ValueError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    # ------------------------------------------------------------------- http

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "X-App-Id": self.app_id}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _unwrap(self, resp: httpx.Response) -> Any:
        """Разворачивает конверт {"data": …}. Непустой errors — ошибка даже при 200."""
        try:
            payload = resp.json()
        except ValueError:
            raise GlobusError(f"HTTP {resp.status_code}: {resp.text[:300]}") from None
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if errors:
            msgs = "; ".join(
                e.get("message", str(e)) if isinstance(e, dict) else str(e) for e in errors
            )
            raise GlobusError(f"HTTP {resp.status_code}: {msgs}")
        if resp.status_code >= 400:
            raise GlobusError(f"HTTP {resp.status_code}: {str(payload)[:300]}")
        return payload.get("data") if isinstance(payload, dict) else payload

    def get(self, path: str, **params: Any) -> Any:
        r = self._http.get(f"{BFF}/{path}", headers=self._headers(), params=params or None)
        return self._unwrap(r)

    def post(self, path: str, body: dict | None = None) -> Any:
        r = self._http.post(f"{BFF}/{path}", headers=self._headers(), json=body or {})
        return self._unwrap(r)

    def patch(self, path: str, body: dict) -> Any:
        r = self._http.patch(f"{BFF}/{path}", headers=self._headers(), json=body)
        return self._unwrap(r)

    def web_post(self, path: str, body: dict) -> tuple[int, Any]:
        """POST к Next.js API-роуту на www.globus.ru (авторизация)."""
        r = self._http.post(f"{WEB}{path}", json=body, headers={"Content-Type": "application/json"})
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, r.text[:500]

    # -------------------------------------------------------------- next data

    def build_id(self, max_age: float = 900.0) -> str:
        """buildId Next.js с главной страницы. Кешируется, протухает после деплоя."""
        if self._build_id and time.time() - self._build_id_ts < max_age:
            return self._build_id
        html = self._http.get(f"{WEB}/", headers={"Accept": "text/html"}).text
        m = re.search(r'"buildId":"([^"]+)"', html)
        if not m:
            raise GlobusError("не удалось определить buildId next.js")
        self._build_id = m.group(1)
        self._build_id_ts = time.time()
        return self._build_id

    def next_data(self, page_path: str, **params: Any) -> dict[str, Any]:
        """Данные SSR-страницы: /_next/data/<buildId><page_path>.json"""
        for attempt in (1, 2):
            url = f"{WEB}/_next/data/{self.build_id()}{page_path}.json"
            r = self._http.get(url, params=params or None,
                               headers={"Accept": "application/json", "X-App-Id": self.app_id})
            if r.status_code == 404 and attempt == 1:
                self._build_id = None  # buildId протух после деплоя
                continue
            if r.status_code >= 400:
                raise GlobusError(f"HTTP {r.status_code} на {url}")
            return r.json()
        raise GlobusError("страница не найдена")

    @staticmethod
    def query_data(next_json: dict[str, Any], key_part: str) -> Any:
        """Достаёт данные react-query по фрагменту queryKey."""
        queries = (
            next_json.get("pageProps", {})
            .get("dehydratedState", {})
            .get("queries", [])
        )
        for q in queries:
            if key_part in json.dumps(q.get("queryKey"), ensure_ascii=False):
                return q.get("state", {}).get("data")
        return None

    # ------------------------------------------------------------------ close

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GlobusClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
