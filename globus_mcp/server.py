"""MCP-сервер к интернет-магазину ГЛОБУС (online.globus.ru)."""

from __future__ import annotations

from typing import Any, Literal

try:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
except ModuleNotFoundError:  # mcp 2.x — FastMCP переименован в MCPServer
    from mcp.server.mcpserver import MCPServer as _Server

from .client import (
    PM_DELIVERY,
    PM_PICKUP_STORE,
    PM_PVZ,
    GlobusClient,
    GlobusError,
)

mcp = _Server("globus")

_client: GlobusClient | None = None


def gc() -> GlobusClient:
    """Ленивый синглтон клиента.

    Создаётся при первом обращении, а не на импорте: импорт пакета не должен
    писать файл состояния в $HOME и открывать HTTP-соединение.
    """
    global _client
    if _client is None:
        _client = GlobusClient()
    return _client


SORTS = ("default", "price_asc", "price_desc", "discount_desc", "name_asc", "name_desc")


# --------------------------------------------------------------------- утилиты


def rub(kop: Any) -> float | None:
    """Копейки → рубли."""
    if kop is None:
        return None
    try:
        return round(int(kop) / 100, 2)
    except (TypeError, ValueError):
        return None


def brief(p: dict[str, Any]) -> dict[str, Any]:
    """Компактное представление товара."""
    name = " ".join(x for x in (p.get("name_optional"), p.get("name_required")) if x).strip()
    out: dict[str, Any] = {
        "id": p.get("id"),
        "name": name or p.get("name"),
        "price": rub(p.get("price")),
        "unit": p.get("unit_price_text") or p.get("unit_basket_text"),
        "url": (p.get("url") or "").rstrip("/") or None,
    }
    if p.get("cost") and p.get("cost") != p.get("price"):
        out["price_before_discount"] = rub(p.get("cost"))
    if p.get("price_per"):
        out["price_per"] = p["price_per"]
    if p.get("quantity"):
        out["in_cart"] = p["quantity"]
    if p.get("quantity_max") is not None:
        out["max_qty"] = p["quantity_max"]
    if p.get("promotions"):
        out["promotions"] = [
            x.get("description") or x.get("name") for x in p["promotions"] if isinstance(x, dict)
        ]
    if p.get("badges"):
        out["badges"] = [b.get("display_text") for b in p["badges"] if b.get("display_text")]
    if p.get("active") is False:
        out["unavailable"] = p.get("active_text") or True
    return {k: v for k, v in out.items() if v not in (None, [], "")}


def page_info(products: dict[str, Any]) -> dict[str, Any]:
    pg = products.get("pagination") or {}
    return {
        "page": pg.get("page"),
        "per_page": pg.get("per_page"),
        "total": pg.get("total"),
        "total_pages": pg.get("total_page"),
    }


def _slug(url_or_slug: str) -> str:
    s = url_or_slug.strip().rstrip("/")
    if "globus.ru" in s:
        s = s.split("globus.ru", 1)[1]
    if "/products/" in s:
        s = s.split("/products/", 1)[1]
    return s.lstrip("/")


# -------------------------------------------------------------- авторизация


@mcp.tool()
def globus_status() -> dict[str, Any]:
    """Текущее состояние сессии: авторизован ли пользователь, какой магазин/способ
    получения выбран, идентификатор устройства."""
    payload = gc().token_payload() or {}
    try:
        ctx = (gc().get("context") or {}).get("context", {})
    except GlobusError as e:
        ctx = {"error": str(e)}
    return {
        "authenticated": bool(payload.get("is_registered")),
        "phone": payload.get("phone_number"),
        "name": payload.get("given_name"),
        "app_id": gc().app_id,
        "context": {
            "purchase_method": ctx.get("purchase_method"),
            "purchase_method_text": {
                PM_PICKUP_STORE: "самовывоз из гипермаркета",
                PM_DELIVERY: "доставка по адресу",
                PM_PVZ: "пункт выдачи",
            }.get(ctx.get("purchase_method")),
            "store_id": ctx.get("store_id"),
            "pvz_id": ctx.get("pvz_id"),
        },
    }


@mcp.tool()
def auth_send_code(phone: str) -> dict[str, Any]:
    """Отправить SMS-код на телефон (формат 79161234567). Первый шаг входа."""
    status, body = gc().web_post("/api/oauth/login", {"phone": phone})
    gc().phone = phone
    return {"status": status, "response": body}


@mcp.tool()
def auth_login(code: str, phone: str | None = None) -> dict[str, Any]:
    """Подтвердить вход кодом из SMS. Полученный токен сохраняется локально
    в ~/.globus-mcp/state.json."""
    phone = phone or gc().phone
    if not phone:
        return {"error": "сначала вызови auth_send_code"}
    status, body = gc().web_post("/api/oauth/login", {"phone": phone, "code": code})
    token = None
    if isinstance(body, dict):
        token = body.get("access_token") or (body.get("data") or {}).get("access_token")
    if token:
        gc().set_token(token)
        return {"ok": True, "authenticated": True}
    return {"ok": False, "status": status, "response": body,
            "hint": "если формат ответа отличается — используй auth_set_token с JWT из браузера"}


@mcp.tool()
def auth_set_token(token: str) -> dict[str, Any]:
    """Импортировать JWT напрямую (взять из __NEXT_DATA__.props.pageProps.token
    на www.globus.ru в авторизованном браузере). Обходной путь, если SMS-логин
    не отработал."""
    gc().set_token(token.strip())
    payload = gc().token_payload() or {}
    return {"ok": True, "phone": payload.get("phone_number"), "name": payload.get("given_name")}


@mcp.tool()
def auth_logout() -> dict[str, Any]:
    """Забыть сохранённый токен (сессия становится анонимной)."""
    gc().set_token(None)
    return {"ok": True}


# ------------------------------------------------------------------- контекст


def _points(items: list[dict[str, Any]], query: str | None) -> list[dict[str, Any]]:
    """Общая выжимка справочников точек: гипермаркеты и ПВЗ устроены одинаково."""
    if query:
        q = query.lower()
        items = [p for p in items if q in f"{p.get('name', '')} {p.get('full_addr', '')}".lower()]
    return [{"id": p.get("id"), "name": p.get("name"), "address": p.get("full_addr")}
            for p in items]


@mcp.tool()
def list_stores(query: str | None = None) -> list[dict[str, Any]]:
    """Гипермаркеты ГЛОБУС. query фильтрует по названию/адресу."""
    data = gc().get("directories/stores") or {}
    return _points(data.get("stores", []), query)


@mcp.tool()
def list_pickup_points(query: str | None = None) -> list[dict[str, Any]]:
    """Пункты выдачи заказов (ПВЗ). query фильтрует по названию/адресу."""
    data = gc().get("directories/pvz") or {}
    return _points(data.get("pvz", []), query)


@mcp.tool()
def set_purchase_method(
    method: Literal["pickup_store", "delivery", "pvz"],
    store_id: int | None = None,
    pvz_id: int | None = None,
    address_id: int | None = None,
) -> dict[str, Any]:
    """Выбрать способ получения и точку. Цены и наличие зависят от него.

    pickup_store — самовывоз из гипермаркета (нужен store_id из list_stores);
    delivery — доставка по адресу (нужен address_id из профиля);
    pvz — пункт выдачи (нужен pvz_id из list_pickup_points).
    """
    ctx: dict[str, Any]
    if method == "pickup_store":
        if not store_id:
            return {"error": "нужен store_id"}
        ctx = {"purchase_method": PM_PICKUP_STORE, "store_id": store_id}
    elif method == "pvz":
        if not pvz_id:
            return {"error": "нужен pvz_id"}
        ctx = {"purchase_method": PM_PVZ, "pvz_id": pvz_id}
    else:
        if not address_id:
            return {"error": "нужен address_id (адрес доставки из профиля)"}
        ctx = {"purchase_method": PM_DELIVERY, "address_id": address_id}
    data = gc().patch("context", {"context": ctx})
    return {"ok": True, "delivery_price": rub((data or {}).get("delivery_price"))}


# --------------------------------------------------------------------- поиск


@mcp.tool()
def search_products(
    query: str,
    page: int = 1,
    per_page: int = 20,
    sort: str = "default",
) -> dict[str, Any]:
    """Поиск товаров. sort: default | price_asc | price_desc | discount_desc |
    name_asc | name_desc."""
    if sort not in SORTS:
        sort = "default"
    data = gc().post(
        "catalog/search:result",
        {
            "query": query,
            "sort": sort,
            "include": ["products"],
            "pagination": {"per_page": min(per_page, 100), "page": page},
        },
    ) or {}
    products = data.get("products") or {}
    return {
        "query": query,
        "found": data.get("products_count_text"),
        "pagination": page_info(products),
        "categories": [{"id": t.get("id"), "name": t.get("name"), "url": t.get("url")}
                       for t in (data.get("tags") or [])],
        "items": [brief(p) for p in products.get("items", [])],
    }


@mcp.tool()
def search_suggest(query: str, limit: int = 5) -> dict[str, Any]:
    """Подсказки поиска: варианты запросов, бренды, категории и топ-товары."""
    data = gc().post(
        "catalog/search:preview",
        {
            "query": query,
            "suggestions_max_count": limit,
            "brands_max_count": limit,
            "products_max_count": limit,
        },
    ) or {}
    return {
        "suggestions": data.get("suggestions", []),
        "brands": [b.get("name") for b in data.get("brands", [])],
        "categories": [{"id": t.get("id"), "name": t.get("name"), "url": t.get("url")}
                       for t in data.get("tags", [])],
        "products": [brief(p) for p in data.get("products", [])],
    }


# -------------------------------------------------------------------- каталог


@mcp.tool()
def list_categories(with_children: bool = True) -> list[dict[str, Any]]:
    """Дерево каталога (верхний уровень и подкатегории)."""
    data = gc().post("header", {"include": ["dropdown", "menu"]}) or {}
    tree = (data.get("menu") or {}).get("catalog", [])
    out = []
    for c in tree:
        node: dict[str, Any] = {"id": c.get("id"), "name": c.get("name")}
        if with_children:
            node["children"] = [
                {"id": s.get("id"), "name": s.get("name"), "url": (s.get("url") or "").rstrip("/")}
                for s in (c.get("categories") or [])
            ]
        out.append(node)
    return out


@mcp.tool()
def category_products(
    category_url: str,
    category_id: int,
    page: int = 1,
    per_page: int = 20,
    sort: str = "default",
    filters: list[str] | None = None,
) -> dict[str, Any]:
    """Товары категории. category_url и category_id берутся из list_categories
    или search_products.categories.

    filters — список строк "<id фильтра>:<id значения>" из category_filters,
    например ["3:17134"] (бренд Parmalat). У флаговых фильтров значение — "да",
    например ["2147483644:да"] — только товары со скидкой.
    """
    if sort not in SORTS:
        sort = "default"
    body: dict[str, Any] = {
        "url": "/" + category_url.strip("/") + "/",
        "category_id": category_id,
        "sort": sort,
        "include": ["category", "products"],
        "pagination": {"per_page": min(per_page, 100), "page": page},
        "is_tag": False,
        "is_edlp": False,
    }
    if filters:
        body["filter"] = {"filter": list(filters)}
    data = gc().post("catalog:product-list", body) or {}
    products = data.get("products") or {}
    return {
        "category": (data.get("category") or {}).get("name"),
        "pagination": page_info(products),
        "items": [brief(p) for p in products.get("items", [])],
    }


@mcp.tool()
def category_filters(category_id: int, values_limit: int = 30) -> list[dict[str, Any]]:
    """Доступные фильтры категории. Значения отдаются как "<id фильтра>:<id значения>" —
    эту строку кладут в аргумент filters у category_products."""
    data = gc().post("catalog/filters", {"category_id": category_id}) or {}
    out = []
    for f in data.get("filters", []):
        fid = f.get("id")
        item: dict[str, Any] = {"id": fid, "name": f.get("name"), "type": f.get("type")}
        if f.get("type") == 1:  # диапазон (цена)
            item["min"] = f.get("min_value")
            item["max"] = f.get("max_value")
        elif f.get("type") == 3:  # флаг
            item["filter"] = f"{fid}:да"
        else:
            values = f.get("values") or []
            item["values_total"] = len(values)
            item["values"] = [
                {"value": v.get("value"), "count": v.get("count"), "filter": f"{fid}:{v.get('id')}"}
                for v in values[:values_limit]
            ]
        out.append(item)
    return out


@mcp.tool()
def product_details(product_url: str) -> dict[str, Any]:
    """Детальная карточка товара. product_url — ссылка или slug вида
    moloko-parmalat-35-1l-26730_ST (берётся из поля url в результатах поиска)."""
    slug = _slug(product_url)
    data = gc().next_data(f"/products/{slug}", productId=slug)
    payload = gc().query_data(data, "product-detail") or {}
    p = (payload.get("data") or payload).get("product") or {}
    if not p:
        return {"error": "товар не найден", "slug": slug}
    brand = p.get("brand")
    desc = p.get("description_attribute") or {}
    desc_text = " ".join(desc.get("value") or []) if isinstance(desc, dict) else None

    out = brief(p)
    out.update(
        {
            "full_name": p.get("name"),
            "brand": brand.get("name") if isinstance(brand, dict) else brand,
            "ean": p.get("ean"),
            "vendor_code": p.get("vendor_code"),
            "description": (desc_text or "").replace("<br/>", "\n").strip() or None,
            "category_id": p.get("main_category_id"),
            "department": p.get("store_department"),
            "images": (p.get("images") or [])[:5],
            "food_energy": p.get("food_energy"),
        }
    )

    attrs: dict[str, Any] = {}
    for group in p.get("attribute_groups") or []:
        if not isinstance(group, dict):
            continue
        for a in group.get("items") or []:
            values = a.get("value") or []
            attrs[a.get("name")] = values[0] if len(values) == 1 else values
    for group_key in ("storage_conditions_attributes", "shelf_life"):
        group = p.get(group_key)
        if isinstance(group, dict):
            for a in group.get("items") or []:
                values = a.get("value") or []
                attrs[a.get("name")] = values[0] if len(values) == 1 else values
    if attrs:
        out["attributes"] = attrs

    return {k: v for k, v in out.items() if v not in (None, [], {}, "")}


# --------------------------------------------------------------- акции и CMS


@mcp.tool()
def promotions() -> dict[str, Any]:
    """Действующие акции и подборки со ссылками на каталог."""
    data = gc().get("content/pages/divorce_shares") or {}
    promos, banners = [], []
    for block in data.get("blocks", []):
        content = block.get("content")
        if block.get("block_type") == "promotions" and content:
            items = content.values() if isinstance(content, dict) else content
            for p in items:
                promos.append(
                    {
                        "id": p.get("id"),
                        "title": p.get("header") or p.get("name"),
                        "dates": p.get("promotion_dates"),
                        "description": p.get("description"),
                        "url": (p.get("button") or {}).get("url"),
                    }
                )
        elif block.get("block_type") == "banners" and isinstance(content, dict):
            for b in content.get("banners", []):
                banners.append({"title": b.get("header") or b.get("catalog_page_title"),
                                "url": b.get("url")})
    return {"promotions": promos, "banners": [b for b in banners if b.get("url")]}


@mcp.tool()
def cms_page(code: str) -> dict[str, Any]:
    """Произвольная CMS-страница BFF по коду (например divorce_shares, main).
    Полезно для подборок и лендингов."""
    data = gc().get(f"content/pages/{code}") or {}
    return {
        "blocks": [
            {"type": b.get("block_type"), "heading": b.get("heading")}
            for b in data.get("blocks", [])
        ],
        "raw": data,
    }


@mcp.tool()
def recommendations(
    block: Literal[
        "popular_tape", "recommendation_tape", "combo_tape",
        "already_bought_tape", "viewed_products_tape",
    ] = "popular_tape",
    product_id: str | None = None,
) -> list[dict[str, Any]]:
    """Ленты товаров: популярное, рекомендации и комбо к товару (нужен product_id),
    ранее купленное и просмотренное (для авторизованной сессии)."""
    block_body: dict[str, Any] = {"block_type": block}
    if product_id:
        block_body["product_id"] = product_id
    data = gc().post("content/product-tapes", {"block_types": [block_body]}) or []
    items: list[dict[str, Any]] = []
    for tape in data if isinstance(data, list) else []:
        for p in tape.get("products", []) or tape.get("items", []):
            items.append(brief(p))
    return items


# -------------------------------------------------------------------- корзина


@mcp.tool()
def cart_view() -> dict[str, Any]:
    """Содержимое корзины и итоги (цены в рублях)."""
    data = gc().get("basket") or {}
    s = data.get("summary") or {}
    items = []
    for group in data.get("product_groups", []):
        for p in group.get("products", []):
            it = brief(p)
            it["quantity"] = p.get("quantity")
            it["sum"] = rub(p.get("price_total") or p.get("cost_total"))
            it["group"] = group.get("group_name")
            items.append(it)
    unavailable = [
        brief(p)
        for g in data.get("unavailable_groups", [])
        for p in g.get("products", [])
    ]
    return {
        "items": items,
        "unavailable": unavailable,
        "summary": {
            "count": s.get("count"),
            "quantity_text": s.get("quantity_text"),
            "total": rub(s.get("total")),
            "products_sum": rub(s.get("basket_price")),
            "discount": rub(s.get("discount")),
            "delivery": rub(s.get("delivery_price")),
            "min_order": rub(s.get("min_order_price")),
            "free_delivery_from": rub(s.get("store_free_delivery")),
            "weight_kg": s.get("basket_weight"),
        },
        "promo_codes": data.get("promo_codes_applied", []),
    }


@mcp.tool()
def cart_set_quantity(product_id: str, quantity: float) -> dict[str, Any]:
    """Задать количество товара в корзине. quantity=0 удаляет товар.
    Для весовых товаров шаг задаётся полем basket_step в карточке."""
    data = gc().post(
        "basket:update-products-quantity",
        {"products": [{"product_id": product_id, "quantity": quantity, "inscriptions": []}]},
    ) or {}
    result = (data.get("result") or [{}])[0]
    return {
        "product_id": result.get("product_id", product_id),
        "quantity": result.get("quantity"),
        "max_qty": result.get("quantity_max"),
        "conditions_diff": data.get("conditions_diff") or None,
    }


@mcp.tool()
def cart_add(product_id: str, quantity: float = 1) -> dict[str, Any]:
    """Добавить товар к тому, что уже лежит в корзине."""
    current = 0.0
    try:
        basket = gc().get("basket") or {}
        for group in basket.get("product_groups", []):
            for p in group.get("products", []):
                if p.get("id") == product_id:
                    current = float(p.get("quantity") or 0)
    except GlobusError:
        pass
    return cart_set_quantity(product_id, current + quantity)


@mcp.tool()
def cart_clear() -> dict[str, Any]:
    """Убрать из корзины все товары."""
    basket = gc().get("basket") or {}
    products = [
        {"product_id": p.get("id"), "quantity": 0, "inscriptions": []}
        for g in basket.get("product_groups", [])
        for p in g.get("products", [])
    ]
    if not products:
        return {"ok": True, "removed": 0}
    gc().post("basket:update-products-quantity", {"products": products})
    return {"ok": True, "removed": len(products)}


@mcp.tool()
def checkout_info() -> dict[str, Any]:
    """Данные оформления: получатель, адреса, доступные слоты доставки, итоги.
    Требует авторизации. Заказ не оформляет.

    Возвращает выжимку, а не сырой ответ: в нём лежат персональные данные
    (ФИО, телефон, полные адреса), и вываливать его целиком в контекст модели
    не нужно.
    """
    data = gc().get("checkout") or {}
    recipient = data.get("recipient") or {}
    summary = data.get("summary") or {}
    slots = []
    for slot in data.get("delivery_intervals") or data.get("intervals") or []:
        if not isinstance(slot, dict):
            continue
        slots.append(
            {
                "date": slot.get("date") or slot.get("day"),
                "interval": slot.get("interval") or slot.get("name"),
                "available": slot.get("available"),
            }
        )
    addresses = [
        {"id": a.get("id"), "address": a.get("full_addr") or a.get("address")}
        for a in data.get("addresses") or []
        if isinstance(a, dict)
    ]
    out = {
        "recipient": {
            "name": recipient.get("name") or recipient.get("given_name"),
            "phone": recipient.get("phone") or recipient.get("phone_number"),
        },
        "addresses": addresses,
        "delivery_slots": slots,
        "summary": {
            "total": rub(summary.get("total")),
            "products_sum": rub(summary.get("basket_price")),
            "discount": rub(summary.get("discount")),
            "delivery": rub(summary.get("delivery_price")),
        },
        "payment_methods": [
            m.get("name") for m in data.get("payment_methods") or [] if isinstance(m, dict)
        ],
    }
    return {k: v for k, v in out.items() if v not in (None, [], {}, "")}


def main() -> None:
    """Точка входа консольной команды globus-mcp: stdio-транспорт."""
    try:
        mcp.run()
    finally:
        if _client is not None:
            _client.close()


if __name__ == "__main__":
    main()
