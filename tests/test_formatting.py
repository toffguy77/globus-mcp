"""Чистые функции нормализации ответов."""

from __future__ import annotations

import pytest

from globus_mcp.server import _slug, brief, page_info, rub


@pytest.mark.parametrize(
    ("kop", "expected"),
    [(12999, 129.99), (0, 0.0), (None, None), ("5000", 50.0), ("нет", None), (-100, -1.0)],
)
def test_rub_converts_kopecks(kop, expected):
    assert rub(kop) == expected


def test_brief_joins_name_parts_and_converts_price():
    item = brief(
        {
            "id": "26730_ST",
            "name_optional": "Молоко",
            "name_required": "Parmalat 3.5% 1л",
            "price": 12999,
            "cost": 15999,
            "url": "/products/moloko-parmalat-26730_ST/",
        }
    )
    assert item["name"] == "Молоко Parmalat 3.5% 1л"
    assert item["price"] == 129.99
    assert item["price_before_discount"] == 159.99
    assert item["url"] == "/products/moloko-parmalat-26730_ST"


def test_brief_omits_price_before_discount_without_discount():
    assert "price_before_discount" not in brief({"id": "1", "price": 100, "cost": 100})


def test_brief_drops_empty_keys():
    item = brief({"id": "1", "name": "Хлеб", "price": None, "promotions": [], "url": ""})
    assert item == {"id": "1", "name": "Хлеб"}


def test_brief_extracts_promotions_and_badges():
    item = brief(
        {
            "id": "1",
            "promotions": [{"description": "2 по цене 1"}, {"name": "Жёлтый ценник"}, "мусор"],
            "badges": [{"display_text": "Хит"}, {}],
        }
    )
    assert item["promotions"] == ["2 по цене 1", "Жёлтый ценник"]
    assert item["badges"] == ["Хит"]


def test_brief_marks_unavailable():
    assert brief({"id": "1", "active": False, "active_text": "нет в наличии"})["unavailable"] == (
        "нет в наличии"
    )
    assert brief({"id": "1", "active": True}).get("unavailable") is None


def test_page_info_maps_pagination():
    assert page_info({"pagination": {"page": 2, "per_page": 20, "total": 41, "total_page": 3}}) == {
        "page": 2,
        "per_page": 20,
        "total": 41,
        "total_pages": 3,
    }
    assert page_info({})["page"] is None


@pytest.mark.parametrize(
    "value",
    [
        "moloko-26730_ST",
        "/products/moloko-26730_ST",
        "https://www.globus.ru/products/moloko-26730_ST/",
        "www.globus.ru/products/moloko-26730_ST",
    ],
)
def test_slug_normalises_urls(value):
    assert _slug(value) == "moloko-26730_ST"
