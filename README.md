# globus-mcp

[![CI](https://github.com/toffguy77/globus-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/toffguy77/globus-mcp/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

MCP-сервер к интернет-магазину **ГЛОБУС** (`online.globus.ru`): поиск, каталог,
карточки товаров, акции, рекомендации и корзина — прямо из диалога с LLM.

Python + MCP SDK, транспорт stdio, запускается локально. 22 инструмента.

> **Не является официальным продуктом ООО «ГИПЕРГЛОБУС».** Проект не аффилирован
> с сетью ГЛОБУС, не одобрен ею и использует незадокументированный внутренний API
> сайта, который может измениться или закрыться в любой момент. Используйте на свой
> риск и в рамках [пользовательского соглашения](https://www.globus.ru/) сайта.

## Что умеет

```
> найди молоко parmalat дешевле 150 рублей и положи два в корзину

  search_products("молоко parmalat", sort="price_asc")
  cart_add("26730_ST", 2)
  cart_view()

  → Молоко Parmalat 3.5% 1л — 129,99 ₽ × 2 = 259,98 ₽
```

Каталог, поиск, карточки, акции и **корзина работают анонимно** — вход по SMS нужен
только для оформления (`checkout_info`) и истории покупок.

## Установка

Нужен Python 3.10+.

```bash
git clone https://github.com/toffguy77/globus-mcp.git
cd globus-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Проверить, что сервер собрался:

```bash
globus-mcp --help 2>/dev/null || echo "сервер запускается только как stdio-процесс"
```

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) или
`%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "globus": {
      "command": "/абсолютный/путь/globus-mcp/.venv/bin/globus-mcp"
    }
  }
}
```

### Claude Code

```bash
claude mcp add globus -- /абсолютный/путь/globus-mcp/.venv/bin/globus-mcp
```

### Любой другой MCP-клиент

Команда — `globus-mcp`, транспорт — stdio, переменные окружения не нужны.

## Инструменты

### Сессия и авторизация

| Инструмент | Назначение |
|---|---|
| `globus_status()` | авторизован ли, какой магазин и способ получения выбран |
| `auth_send_code(phone)` | отправить SMS-код (формат `79161234567`) |
| `auth_login(code)` | подтвердить вход кодом |
| `auth_set_token(token)` | импортировать JWT из браузера (обходной путь) |
| `auth_logout()` | забыть токен, вернуться к анонимной сессии |

### Контекст получения

От него зависят цены и наличие — выставляйте его до поиска.

| Инструмент | Назначение |
|---|---|
| `list_stores(query)` | гипермаркеты |
| `list_pickup_points(query)` | пункты выдачи |
| `set_purchase_method(method, store_id/pvz_id/address_id)` | `pickup_store` \| `delivery` \| `pvz` |

### Каталог и поиск

| Инструмент | Назначение |
|---|---|
| `search_products(query, page, per_page, sort)` | поиск товаров |
| `search_suggest(query)` | подсказки, бренды, категории, топ-товары |
| `list_categories()` | дерево каталога |
| `category_products(category_url, category_id, filters, …)` | товары категории |
| `category_filters(category_id)` | доступные фильтры и готовые строки для `filters` |
| `product_details(product_url)` | детальная карточка: состав, БЖУ, срок годности |

### Контент

| Инструмент | Назначение |
|---|---|
| `promotions()` | действующие акции со ссылками на каталог |
| `cms_page(code)` | произвольная CMS-страница |
| `recommendations(block, product_id)` | популярное, рекомендации, комбо, ранее купленное |

### Корзина

| Инструмент | Назначение |
|---|---|
| `cart_view()` | состав и итоги |
| `cart_add(product_id, quantity)` | добавить к текущему количеству |
| `cart_set_quantity(product_id, quantity)` | задать количество, `0` — удалить |
| `cart_clear()` | очистить |
| `checkout_info()` | получатель, адреса, слоты доставки (только авторизованно) |

**Оформление заказа и оплата не реализованы намеренно** — сервер не может потратить
ваши деньги. Собранную корзину видно на сайте под тем же аккаунтом.

## Авторизация

Большинству сценариев вход не нужен. Если всё-таки нужен:

```
auth_send_code("79161234567")   # придёт SMS
auth_login("1234")              # код из SMS
```

Второй шаг проверен не до конца (см. [Ограничения](#ограничения)). Обходной путь —
взять JWT из авторизованного браузера: открыть `www.globus.ru`, в консоли выполнить

```js
__NEXT_DATA__.props.pageProps.token
```

и передать строку в `auth_set_token(...)`. Токен живёт около часа.

## Безопасность

Состояние сессии лежит в `~/.globus-mcp/state.json` с правами `600`:
идентификатор устройства (`app_id`), телефон последнего входа и **JWT в открытом виде**.

* Не коммитьте этот файл и не прикладывайте его к issue.
* `auth_logout()` стирает токен.
* Каталог состояния переопределяется переменной `GLOBUS_MCP_HOME`.
* Удаление файла целиком даёт новую анонимную сессию с пустой корзиной.

`checkout_info()` возвращает персональные данные (имя, телефон, адреса) — они попадут
в контекст модели. Инструмент отдаёт выжимку, а не сырой ответ API, но осторожность
не помешает.

## Ограничения

* **Сайт закрыт для зарубежных IP** — сервер должен работать с российского адреса.
* **Рецептов у ГЛОБУСа нет** — ни раздела, ни CMS-страницы. Ближайшая замена —
  `recommendations(block="combo_tape")` и подборки в `promotions()`.
* **Второй шаг SMS-логина не подтверждён на живом входе.** Первый шаг
  (`POST /api/oauth/login` с `{phone}`) проверен, формат подтверждения кодом
  (`{phone, code}`) угадан. Если он не сработает — используйте `auth_set_token`.
* Избранное и история заказов — эндпоинты не найдены, не реализованы.
* API незадокументирован: Swagger/OpenAPI не публикуется, всё разведано вручную.
  Ломается без предупреждения.

## Разработка

```bash
pip install -e ".[dev]"
pytest          # 76 тестов, все офлайн
ruff check .
```

Тесты не ходят в сеть и не трогают `~/.globus-mcp`: транспорт подменяется
`httpx.MockTransport`, каталог состояния — временным.

Инструменты — обычные функции модуля, поэтому отлаживаются без запуска сервера:

```python
from globus_mcp.server import search_products
print(search_products("молоко", per_page=3))
```

Устройство API и разведанные эндпоинты — [docs/API.md](docs/API.md).
Как присылать изменения — [CONTRIBUTING.md](CONTRIBUTING.md).
Изменения в `main` принимаются **только через pull request**.

## Лицензия

[MIT](LICENSE) © Dmitry Spasskiy
