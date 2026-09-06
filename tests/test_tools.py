"""Инструменты MCP поверх замоканного транспорта."""

from __future__ import annotations

import base64
import json

import pytest
from conftest import bff, web

from globus_mcp import server as s
from globus_mcp.client import GlobusError


def envelope(data):
    return {"data": data, "errors": []}


# ------------------------------------------------------------------- сессия


def test_globus_status_anonymous(fake_client, recorder):
    recorder.route("GET", bff("context"), envelope({"context": {"purchase_method": 1,
                                                               "store_id": 7}}))
    out = s.globus_status()
    assert out["authenticated"] is False
    assert out["context"]["purchase_method_text"] == "самовывоз из гипермаркета"
    assert out["context"]["store_id"] == 7
    assert out["app_id"] == fake_client.app_id


def test_globus_status_survives_context_error(fake_client, recorder):
    """Статус должен отвечать даже когда BFF недоступен."""
    recorder.route("GET", bff("context"), {"errors": [{"message": "упал"}]})
    assert s.globus_status()["authenticated"] is False


def test_globus_status_reads_jwt(fake_client, recorder):
    body = base64.urlsafe_b64encode(
        json.dumps({"is_registered": True, "phone_number": "79161234567",
                    "given_name": "Дмитрий"}).encode()
    ).decode().rstrip("=")
    fake_client.set_token(f"h.{body}.s")
    recorder.route("GET", bff("context"), envelope({"context": {}}))
    out = s.globus_status()
    assert out["authenticated"] is True
    assert out["name"] == "Дмитрий"


def test_auth_send_code_stores_phone(fake_client, recorder):
    recorder.route("POST", web("/api/oauth/login"), {"ok": True})
    s.auth_send_code("79161234567")
    assert fake_client.phone == "79161234567"


def test_auth_login_requires_phone(fake_client):
    assert "error" in s.auth_login("1234")


def test_auth_login_saves_token(fake_client, recorder):
    recorder.route("POST", web("/api/oauth/login"), {"access_token": "JWT"})
    fake_client.phone = "79161234567"
    assert s.auth_login("1234")["ok"] is True
    assert fake_client.token == "JWT"


def test_auth_login_reports_unknown_response_shape(fake_client, recorder):
    """Второй шаг SMS-входа не подтверждён — подсказка про обходной путь обязана быть."""
    recorder.route("POST", web("/api/oauth/login"), {"unexpected": True})
    fake_client.phone = "79161234567"
    out = s.auth_login("1234")
    assert out["ok"] is False
    assert "auth_set_token" in out["hint"]


def test_auth_set_token_and_logout(fake_client):
    body = base64.urlsafe_b64encode(json.dumps({"given_name": "Дмитрий"}).encode()).decode()
    s.auth_set_token(f"h.{body.rstrip('=')}.s  ")
    assert fake_client.token.endswith(".s")
    s.auth_logout()
    assert fake_client.token is None


# ------------------------------------------------------------------ контекст


def test_list_stores_filters_by_query(fake_client, recorder):
    recorder.route("GET", bff("directories/stores"), envelope({"stores": [
        {"id": 1, "name": "Щёлково", "full_addr": "Московская обл."},
        {"id": 2, "name": "Климовск", "full_addr": "Подольск"},
    ]}))
    assert [x["id"] for x in s.list_stores("подольск")] == [2]


def test_list_pickup_points(fake_client, recorder):
    recorder.route("GET", bff("directories/pvz"), envelope({"pvz": [
        {"id": 9, "name": "ПВЗ Тверская", "full_addr": "Москва"},
    ]}))
    assert s.list_pickup_points()[0]["address"] == "Москва"


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"method": "pickup_store", "store_id": 7}, {"purchase_method": 1, "store_id": 7}),
        ({"method": "pvz", "pvz_id": 9}, {"purchase_method": 3, "pvz_id": 9}),
        ({"method": "delivery", "address_id": 3}, {"purchase_method": 2, "address_id": 3}),
    ],
)
def test_set_purchase_method_sends_context(fake_client, recorder, kwargs, expected):
    recorder.route("PATCH", bff("context"), envelope({"delivery_price": 29900}))
    out = s.set_purchase_method(**kwargs)
    assert recorder.body() == {"context": expected}
    assert out["delivery_price"] == 299.0


@pytest.mark.parametrize(
    "kwargs",
    [{"method": "pickup_store"}, {"method": "pvz"}, {"method": "delivery"}],
)
def test_set_purchase_method_validates_ids(fake_client, kwargs):
    assert "error" in s.set_purchase_method(**kwargs)


# -------------------------------------------------------------------- поиск


SEARCH_RESPONSE = envelope({
    "products_count_text": "найдено 41",
    "tags": [{"id": 5, "name": "Молоко", "url": "/catalog/moloko/"}],
    "products": {
        "pagination": {"page": 1, "per_page": 2, "total": 41, "total_page": 21},
        "items": [
            {"id": "1", "name_required": "Молоко 3.2%", "price": 8999, "url": "/products/a/"},
            {"id": "2", "name_required": "Молоко 2.5%", "price": 7999, "url": "/products/b/"},
        ],
    },
})


def test_search_products_shapes_request_and_response(fake_client, recorder):
    recorder.route("POST", bff("catalog/search:result"), SEARCH_RESPONSE)
    out = s.search_products("молоко", per_page=2, sort="price_asc")
    assert recorder.body() == {
        "query": "молоко",
        "sort": "price_asc",
        "include": ["products"],
        "pagination": {"per_page": 2, "page": 1},
    }
    assert out["found"] == "найдено 41"
    assert out["pagination"]["total_pages"] == 21
    assert [i["price"] for i in out["items"]] == [89.99, 79.99]
    assert out["categories"][0]["name"] == "Молоко"


def test_search_products_rejects_unknown_sort(fake_client, recorder):
    recorder.route("POST", bff("catalog/search:result"), SEARCH_RESPONSE)
    s.search_products("молоко", sort="совершенно любой")
    assert recorder.body()["sort"] == "default"


def test_search_products_caps_per_page_at_100(fake_client, recorder):
    recorder.route("POST", bff("catalog/search:result"), SEARCH_RESPONSE)
    s.search_products("молоко", per_page=500)
    assert recorder.body()["pagination"]["per_page"] == 100


def test_search_suggest(fake_client, recorder):
    recorder.route("POST", bff("catalog/search:preview"), envelope({
        "suggestions": ["молоко", "молоко козье"],
        "brands": [{"name": "Parmalat"}],
        "tags": [{"id": 5, "name": "Молоко", "url": "/catalog/moloko/"}],
        "products": [{"id": "1", "name_required": "Молоко", "price": 8999}],
    }))
    out = s.search_suggest("моло")
    assert out["brands"] == ["Parmalat"]
    assert out["products"][0]["price"] == 89.99


# ------------------------------------------------------------------- каталог


def test_list_categories(fake_client, recorder):
    recorder.route("POST", bff("header"), envelope({"menu": {"catalog": [
        {"id": 1, "name": "Молочное", "categories": [
            {"id": 5, "name": "Молоко", "url": "/catalog/moloko/"}]},
    ]}}))
    tree = s.list_categories()
    assert tree[0]["children"][0]["url"] == "/catalog/moloko"
    assert s.list_categories(with_children=False)[0].get("children") is None


def test_category_products_builds_url_and_filters(fake_client, recorder):
    recorder.route("POST", bff("catalog:product-list"), envelope({
        "category": {"name": "Молоко"},
        "products": {"pagination": {"page": 1}, "items": [
            {"id": "1", "name_required": "Молоко", "price": 8999}]},
    }))
    out = s.category_products("catalog/moloko", 5, filters=["3:17134"])
    body = recorder.body()
    assert body["url"] == "/catalog/moloko/"
    assert body["category_id"] == 5
    assert body["filter"] == {"filter": ["3:17134"]}
    assert out["category"] == "Молоко"


def test_category_products_omits_filter_when_absent(fake_client, recorder):
    recorder.route("POST", bff("catalog:product-list"), envelope({"products": {"items": []}}))
    s.category_products("/catalog/moloko/", 5)
    assert "filter" not in recorder.body()


def test_category_filters_builds_filter_strings(fake_client, recorder):
    recorder.route("POST", bff("catalog/filters"), envelope({"filters": [
        {"id": 1, "name": "Цена", "type": 1, "min_value": 100, "max_value": 9000},
        {"id": 2147483644, "name": "Со скидкой", "type": 3},
        {"id": 3, "name": "Бренд", "type": 2, "values": [
            {"id": 17134, "value": "Parmalat", "count": 12},
            {"id": 17135, "value": "Домик в деревне", "count": 7},
        ]},
    ]}))
    price, flag, brand = s.category_filters(5, values_limit=1)
    assert (price["min"], price["max"]) == (100, 9000)
    assert flag["filter"] == "2147483644:да"
    assert brand["values_total"] == 2
    assert len(brand["values"]) == 1
    assert brand["values"][0]["filter"] == "3:17134"


# ------------------------------------------------------ карточка товара (SSR)


def next_page(product):
    return {"pageProps": {"dehydratedState": {"queries": [
        {"queryKey": ["product-detail", "x"], "state": {"data": {"product": product}}},
    ]}}}


def test_product_details_reads_ssr(fake_client, recorder):
    recorder.route("GET", web("/"), '<script>{"buildId":"B1"}</script>')
    recorder.route("GET", web("/_next/data/B1/products/moloko-26730_ST.json"), next_page({
        "id": "26730_ST",
        "name": "Молоко Parmalat 3.5% 1л",
        "name_required": "Молоко Parmalat",
        "price": 12999,
        "ean": "8001234567890",
        "brand": {"name": "Parmalat"},
        "description_attribute": {"value": ["Отборное молоко.<br/>Пастеризованное."]},
        "attribute_groups": [{"items": [
            {"name": "Жирность", "value": ["3.5%"]},
            {"name": "Состав", "value": ["молоко", "закваска"]},
        ]}],
        "shelf_life": {"items": [{"name": "Срок годности", "value": ["10 суток"]}]},
        "images": ["1.jpg", "2.jpg"],
    }))
    out = s.product_details("https://www.globus.ru/products/moloko-26730_ST/")
    assert out["brand"] == "Parmalat"
    assert out["price"] == 129.99
    assert out["description"] == "Отборное молоко.\nПастеризованное."
    assert out["attributes"]["Жирность"] == "3.5%"
    assert out["attributes"]["Состав"] == ["молоко", "закваска"]
    assert out["attributes"]["Срок годности"] == "10 суток"


def test_product_details_reports_missing_product(fake_client, recorder):
    recorder.route("GET", web("/"), '<script>{"buildId":"B1"}</script>')
    recorder.route("GET", web("/_next/data/B1/products/unknown-slug.json"), next_page({}))
    out = s.product_details("unknown-slug")
    assert out["error"] == "товар не найден"
    assert out["slug"] == "unknown-slug"


# ------------------------------------------------------------- акции и ленты


def test_promotions_splits_blocks(fake_client, recorder):
    recorder.route("GET", bff("content/pages/divorce_shares"), envelope({"blocks": [
        {"block_type": "promotions", "content": {"a": {
            "id": 1, "header": "Неделя молока", "promotion_dates": "1–7 сентября",
            "button": {"url": "/catalog/moloko/"}}}},
        {"block_type": "banners", "content": {"banners": [
            {"header": "Скидки", "url": "/sale/"},
            {"header": "Без ссылки"},
        ]}},
    ]}))
    out = s.promotions()
    assert out["promotions"][0]["title"] == "Неделя молока"
    assert out["banners"] == [{"title": "Скидки", "url": "/sale/"}]


def test_cms_page_returns_block_map_and_raw(fake_client, recorder):
    recorder.route("GET", bff("content/pages/main"),
                   envelope({"blocks": [{"block_type": "banners", "heading": "Главное"}]}))
    out = s.cms_page("main")
    assert out["blocks"] == [{"type": "banners", "heading": "Главное"}]
    assert "raw" in out


def test_recommendations(fake_client, recorder):
    recorder.route("POST", bff("content/product-tapes"), envelope([
        {"products": [{"id": "1", "name_required": "Хлеб", "price": 4999}]},
    ]))
    out = s.recommendations(block="combo_tape", product_id="26730_ST")
    assert recorder.body() == {"block_types": [
        {"block_type": "combo_tape", "product_id": "26730_ST"}]}
    assert out[0]["price"] == 49.99


# ------------------------------------------------------------------- корзина


BASKET = envelope({
    "product_groups": [{"group_name": "Бакалея", "products": [
        {"id": "1", "name_required": "Молоко", "price": 8999, "quantity": 2,
         "price_total": 17998},
    ]}],
    "unavailable_groups": [{"products": [{"id": "9", "name_required": "Нет в наличии"}]}],
    "summary": {"count": 1, "total": 17998, "basket_price": 19998, "discount": 2000,
                "delivery_price": 0, "basket_weight": 2.1},
})


def test_cart_view(fake_client, recorder):
    recorder.route("GET", bff("basket"), BASKET)
    out = s.cart_view()
    assert out["items"][0]["sum"] == 179.98
    assert out["items"][0]["group"] == "Бакалея"
    assert out["unavailable"][0]["id"] == "9"
    assert out["summary"]["total"] == 179.98
    assert out["summary"]["discount"] == 20.0


def test_cart_set_quantity(fake_client, recorder):
    recorder.route("POST", bff("basket:update-products-quantity"),
                   envelope({"result": [{"product_id": "1", "quantity": 3, "quantity_max": 10}]}))
    out = s.cart_set_quantity("1", 3)
    assert recorder.body() == {"products": [
        {"product_id": "1", "quantity": 3, "inscriptions": []}]}
    assert out["quantity"] == 3
    assert out["max_qty"] == 10


def test_cart_add_accumulates_existing_quantity(fake_client, recorder):
    recorder.route("GET", bff("basket"), BASKET)
    recorder.route("POST", bff("basket:update-products-quantity"),
                   envelope({"result": [{"product_id": "1", "quantity": 3}]}))
    s.cart_add("1", 1)
    assert recorder.body(1)["products"][0]["quantity"] == 3.0


def test_cart_add_when_basket_unavailable_starts_from_zero(fake_client, recorder):
    recorder.route("GET", bff("basket"), {"errors": [{"message": "нет"}]})
    recorder.route("POST", bff("basket:update-products-quantity"),
                   envelope({"result": [{"product_id": "1", "quantity": 2}]}))
    s.cart_add("1", 2)
    assert recorder.body(1)["products"][0]["quantity"] == 2.0


def test_cart_clear_zeroes_every_product(fake_client, recorder):
    recorder.route("GET", bff("basket"), BASKET)
    recorder.route("POST", bff("basket:update-products-quantity"), envelope({"result": []}))
    assert s.cart_clear() == {"ok": True, "removed": 1}
    assert recorder.body(1)["products"] == [
        {"product_id": "1", "quantity": 0, "inscriptions": []}]


def test_cart_clear_on_empty_basket_makes_no_write(fake_client, recorder):
    recorder.route("GET", bff("basket"), envelope({"product_groups": []}))
    assert s.cart_clear() == {"ok": True, "removed": 0}
    assert len(recorder.requests) == 1


# ------------------------------------------------------------------ checkout


def test_checkout_info_returns_summary_not_raw_payload(fake_client, recorder):
    """Сырой ответ содержит ПДн — наружу должна уходить выжимка."""
    recorder.route("GET", bff("checkout"), envelope({
        "recipient": {"name": "Дмитрий", "phone": "79161234567", "passport": "секрет"},
        "addresses": [{"id": 3, "full_addr": "Москва, Тверская 1", "flat": "12"}],
        "delivery_intervals": [{"date": "2026-09-07", "interval": "10:00–12:00",
                                "available": True}],
        "summary": {"total": 17998, "delivery_price": 29900},
        "payment_methods": [{"name": "Картой онлайн"}],
        "internal_debug": {"token": "не должен утечь"},
    }))
    out = s.checkout_info()
    assert out["recipient"] == {"name": "Дмитрий", "phone": "79161234567"}
    assert out["addresses"] == [{"id": 3, "address": "Москва, Тверская 1"}]
    assert out["delivery_slots"][0]["interval"] == "10:00–12:00"
    assert out["summary"]["delivery"] == 299.0
    assert out["payment_methods"] == ["Картой онлайн"]
    assert "internal_debug" not in json.dumps(out, ensure_ascii=False)
    assert "passport" not in json.dumps(out, ensure_ascii=False)


def test_checkout_info_requires_auth(fake_client, recorder):
    recorder.route("GET", bff("checkout"), {"errors": [{"message": "unauthorized"}]}, status=401)
    with pytest.raises(GlobusError):
        s.checkout_info()
