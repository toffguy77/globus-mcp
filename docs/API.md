# Внутреннее API online.globus.ru

Swagger/OpenAPI сеть не публикует. Всё ниже — результат ручной разведки: каждый
эндпоинт и формат тела проверялся на живом API, парсеры прогонялись на реальных
фрагментах ответов. Документ описывает то, на что опирается `globus_mcp/client.py`,
и может устареть в любой момент.

## Топология

```
online.globus.ru  →  www.globus.ru            Next.js SSR, отдаёт HTML и /_next/data
                     digitalone.globus.ru     BFF, отдаёт JSON
```

Базовый URL BFF: `https://digitalone.globus.ru/d1-front-bff/api-web/v1`

## Заголовки

| Заголовок | Обязателен | Смысл |
|---|---|---|
| `X-App-Id` | да | UUID устройства. К нему привязаны корзина и контекст получения. |
| `Authorization: Bearer <JWT>` | нет | Без него сессия анонимная. |
| `Content-Type: application/json` | да для POST/PATCH | |

**Без авторизации API работает.** Поиск, каталог, карточки, акции и даже корзина
доступны анонимно. Токен нужен только для `checkout`, истории покупок и адресов
доставки. Это ключевое свойство: сервер полезен сразу после установки.

## Формат ответа

Всё завёрнуто в конверт:

```json
{ "data": { … }, "errors": [] }
```

`errors` может быть непустым **при HTTP 200** — это всё равно ошибка. Клиент
разворачивает конверт и поднимает `GlobusError` в обоих случаях
(`GlobusClient._unwrap`).

**Все цены — в копейках**: `12999` == 129,99 ₽.

## Контекст получения

`purchase_method` определяет цены и наличие:

| Значение | Способ | Обязательное поле |
|---|---|---|
| `1` | самовывоз из гипермаркета | `store_id` |
| `2` | доставка по адресу | `address_id` |
| `3` | пункт выдачи | `pvz_id` |

Меняется через `PATCH context` с телом `{"context": {"purchase_method": 1, "store_id": 7}}`.

## Эндпоинты

| Метод | Путь | Назначение |
|---|---|---|
| POST | `catalog/search:result` | поиск: `{query, sort, include:["products"], pagination}` |
| POST | `catalog/search:preview` | подсказки поиска |
| POST | `catalog:product-list` | товары категории — **двоеточие после `catalog`, не слеш** |
| POST | `catalog/filters` | фильтры категории |
| POST | `header` | меню каталога и мини-корзина: `{include:["dropdown","menu","basket"]}` |
| GET / PATCH | `context` | текущий способ получения и его смена |
| GET | `directories/stores` | справочник гипермаркетов |
| GET | `directories/pvz` | справочник ПВЗ |
| GET | `basket` | корзина |
| POST | `basket:update-products-quantity` | добавить/изменить/удалить (`quantity: 0`) |
| GET | `checkout` | данные оформления (401 без авторизации) |
| GET | `content/pages/{code}` | CMS-страницы (`divorce_shares` — акции) |
| POST | `content/product-tapes` | ленты рекомендаций |

### Сортировка

`default`, `price_asc`, `price_desc`, `discount_desc`, `name_asc`, `name_desc`.

### Пагинация

`{"pagination": {"page": 1, "per_page": 20}}`. `per_page` до `100` работает.

### Фильтры каталога

Передаются как `{"filter": {"filter": ["3:17134"]}}` — пара
`<id фильтра>:<id значения>`.

* `3:17134` — бренд Parmalat;
* у флаговых фильтров значение — буквально `да`: `2147483644:да` (только со скидкой);
* фильтр типа `1` — диапазон (цена), у него `min_value`/`max_value`.

Строки собирает инструмент `category_filters` — вручную их конструировать не нужно.

## Детальная карточка товара (SSR)

В BFF её нет. Данные берутся из Next.js:

```
GET https://www.globus.ru/_next/data/<buildId>/products/<slug>.json?productId=<slug>
```

`buildId` вытаскивается регуляркой `"buildId":"([^"]+)"` с главной страницы,
кешируется на 15 минут и **перечитывается при первом 404** — после деплоя сайта
старый `buildId` перестаёт существовать.

Внутри ответа полезное лежит в дегидратированном состоянии react-query:

```
pageProps.dehydratedState.queries[] → queryKey содержит "product-detail" → state.data.product
```

Это делает `GlobusClient.query_data(next_json, "product-detail")`.

## Авторизация

Двухшаговый вход по SMS через Next.js API-роут на `www.globus.ru`, не через BFF:

```
POST https://www.globus.ru/api/oauth/login   {"phone": "79161234567"}          → SMS
POST https://www.globus.ru/api/oauth/login   {"phone": "...", "code": "1234"}  → access_token
```

**Первый шаг подтверждён** — запрос без `phone` отвечает `400 phone field required`.
**Второй шаг угадан и не проверен на живом входе.** Если формат ответа отличается,
рабочий обходной путь — забрать JWT из авторизованного браузера:

```js
__NEXT_DATA__.props.pageProps.token
```

и передать в `auth_set_token`. Срок жизни токена — около часа (`exp` в JWT).
Подпись клиент не проверяет: payload декодируется только чтобы показать, кто вошёл.

## Что найти не удалось

* избранное;
* история заказов;
* рецепты — раздела нет на сайте вовсе, `content/pages/recipes` отдаёт пустые блоки;
* оформление заказа и оплата **не реализованы намеренно**, хотя `GET checkout`
  разведан.

## Как проверять изменения

Юнит-тесты офлайновые и живой API не покрывают. Если правите транспорт, прогоните
инструменты руками с российского IP:

```python
from globus_mcp.server import search_products, product_details
print(search_products("молоко", per_page=3))
print(product_details("<url из результата выше>"))
```

Заметили расхождение с этим документом — поправьте его в том же pull request.
