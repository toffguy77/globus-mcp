# Изменения

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версии — по [семантическому версионированию](https://semver.org/lang/ru/).

## [Не выпущено]

## [0.1.0] — 2026-09-06

Первый публичный выпуск.

### Добавлено

* MCP-сервер к `online.globus.ru` на stdio-транспорте, 22 инструмента:
  сессия и авторизация, контекст получения, каталог и поиск, контент, корзина.
* `GlobusClient` — транспорт к BFF `digitalone.globus.ru` и к SSR Next.js
  для детальных карточек товаров, с кешем `buildId` и повтором при 404.
* Анонимный режим: поиск, каталог, акции и корзина работают без авторизации.
* Вход по SMS и импорт JWT из браузера как обходной путь.
* Хранение состояния сессии в `~/.globus-mcp/state.json` с правами `600`,
  каталог переопределяется переменной `GLOBUS_MCP_HOME`.
* Совместимость с `mcp` 1.x (`FastMCP`) и 2.x (`MCPServer`) — импорт выбирается
  автоматически.
* 76 офлайн-тестов на `httpx.MockTransport`, линтер `ruff`, CI на Python 3.10–3.13.
* Документация: `README.md`, `docs/API.md` с разведкой внутреннего API,
  `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`.

### Не реализовано намеренно

* Оформление заказа и оплата.

[Не выпущено]: https://github.com/toffguy77/globus-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/toffguy77/globus-mcp/releases/tag/v0.1.0
