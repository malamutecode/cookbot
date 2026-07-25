"""Unit tests for the Frisco live-search path (STEP 50).

Hermetic — no network. The HTTP layer is replaced with an ``httpx.MockTransport``
so the fan-out, failure containment and concurrency cap are exercised for real
(actual ``asyncio.gather`` over a real ``AsyncClient``) without leaving the process.

Payload shapes mirror the live API as verified on 2026-07-25: search hits carry
``ordererScore`` + a nested ``product``, and notably **omit** ``productUrl`` and
``keywords``.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from delivery_shops.base import supports_search
from delivery_shops.shops.frisco import FriscoShop, parse_search_response

_SEARCH_URL = "https://frisco.test/query"


def _hit(product_id: str, name: str, *, score: float = 1.0, **overrides) -> dict:
    """One ``products[]`` entry shaped like the live search response."""
    product = {
        "id": product_id,
        "name": {"pl": name, "en": name},
        "brand": "LUMIKO",
        "grammage": 0.2,
        "unitOfMeasure": "Kilogram",
        "price": {"price": 6.19},
        "imageUrl": "https://img.test/x.png",
        "isAvailable": True,
        "primaryCategory": {"categoryId": "19125", "name": {"pl": "Tradycyjne"}},
        "categories": [
            {"name": {"pl": "Nabiał, jaja, sery"}},
            {"name": {"pl": "Masło i margaryny"}},
            {"name": {"pl": "Tradycyjne"}},
        ],
    }
    product.update(overrides)
    return {"productId": product_id, "ordererScore": score, "product": product}


def _payload(*hits: dict) -> dict:
    return {"products": list(hits), "totalCount": len(hits)}


# --------------------------------------------------------------------------
# Mapping
# --------------------------------------------------------------------------


def test_reconstructs_product_url_from_id() -> None:
    """Search results have no productUrl — it must be rebuilt (slugless form)."""
    matches = parse_search_response(_payload(_hit("139312", "Masło")), "masło", "frisco", 5)
    assert matches[0].product.url == "https://www.frisco.pl/pid,139312/stn,product"


def test_maps_full_category_ancestry_and_carries_frisco_score() -> None:
    """The whole chain is kept: the leaf alone loses the searchable word."""
    matches = parse_search_response(_payload(_hit("1", "Masło Ekstra", score=14015.001)), "masło", "frisco", 5)
    assert matches[0].product.category == "Nabiał, jaja, sery Masło i margaryny Tradycyjne"
    assert matches[0].score == pytest.approx(14015.001)
    assert matches[0].ingredient == "masło"
    assert matches[0].shop == "frisco"


def test_category_keeps_the_semantic_anchor_not_just_the_leaf() -> None:
    """Real regression: 'Pomidory malinowe' has primaryCategory 'Malinowe', but
    the ancestry contains 'Pomidory' — the word a human actually searched for."""
    hit = _hit(
        "1",
        "Pomidory malinowe 3-4szt.",
        primaryCategory={"name": {"pl": "Malinowe"}},
        categories=[
            {"name": {"pl": "Warzywa i owoce"}},
            {"name": {"pl": "Pomidory"}},
            {"name": {"pl": "Duże"}},
            {"name": {"pl": "Malinowe"}},
        ],
    )
    category = parse_search_response(_payload(hit), "pomidory", "frisco", 5)[0].product.category
    assert "Pomidory" in category
    assert "Malinowe" in category  # the leaf is not discarded


def test_category_falls_back_to_leaf_without_a_chain() -> None:
    hit = _hit("1", "Masło", categories=None)
    matches = parse_search_response(_payload(hit), "masło", "frisco", 5)
    assert matches[0].product.category == "Tradycyjne"


def test_category_dedupes_repeated_names_in_the_chain() -> None:
    """Salt sits under 'Sól' via two trees; the name must not repeat."""
    hit = _hit(
        "1",
        "Sól warzona jodowana",
        categories=[
            {"name": {"pl": "Spiżarnia"}},
            {"name": {"pl": "Sól"}},
            {"name": {"pl": "Przyprawy i zioła"}},
            {"name": {"pl": "Sól"}},
        ],
    )
    category = parse_search_response(_payload(hit), "sól", "frisco", 5)[0].product.category
    assert category.split().count("Sól") == 1


def test_tolerates_missing_price_and_keywords() -> None:
    """Feeds and search both omit price on some rows; search never sends keywords."""
    matches = parse_search_response(_payload(_hit("1", "Masło", price=None)), "masło", "frisco", 5)
    assert matches[0].product.price is None
    assert matches[0].product.keywords == ""


def test_preserves_api_ranking_order() -> None:
    """Frisco pre-ranks; we must not re-sort."""
    payload = _payload(_hit("a", "A", score=5.0), _hit("b", "B", score=99.0))
    ids = [m.product.id for m in parse_search_response(payload, "q", "frisco", 5)]
    assert ids == ["a", "b"]


def test_respects_limit_and_skips_malformed_hits() -> None:
    payload = {"products": [_hit("a", "A"), "junk", {"product": None}, _hit("b", "B")]}
    matches = parse_search_response(payload, "q", "frisco", limit=1)
    assert [m.product.id for m in matches] == ["a"]


def test_empty_response_yields_no_matches() -> None:
    assert parse_search_response({"products": []}, "q", "frisco", 5) == []


# --------------------------------------------------------------------------
# Fan-out
# --------------------------------------------------------------------------


def _shop_with(handler, **kwargs) -> FriscoShop:
    """A FriscoShop whose pooled client is backed by a MockTransport."""
    shop = FriscoShop(search_url=_SEARCH_URL, **kwargs)
    shop._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return shop


def test_frisco_advertises_the_search_capability() -> None:
    assert supports_search(FriscoShop())


async def test_search_many_resolves_every_ingredient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["search"]
        return httpx.Response(200, json=_payload(_hit(f"id-{query}", query.title())))

    shop = _shop_with(handler)
    result = await shop.search_many(["masło", "cebula", "sól"])

    assert set(result) == {"masło", "cebula", "sól"}
    assert result["cebula"][0].product.id == "id-cebula"
    await shop.aclose()


async def test_search_uses_one_based_page_index() -> None:
    """pageIndex=0 is an HTTP 400 on the live API."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["pageIndex"])
        return httpx.Response(200, json=_payload())

    shop = _shop_with(handler)
    await shop.search("masło")
    assert seen == ["1"]
    await shop.aclose()


async def test_one_failing_ingredient_does_not_sink_the_batch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["search"] == "cebula":
            return httpx.Response(500)
        return httpx.Response(200, json=_payload(_hit("ok", "OK")))

    shop = _shop_with(handler)
    result = await shop.search_many(["masło", "cebula", "sól"])

    assert result["cebula"] == []  # contained: reported as unmatched, not raised
    assert result["masło"][0].product.id == "ok"
    assert result["sól"][0].product.id == "ok"
    await shop.aclose()


async def test_total_failure_raises_so_the_caller_can_fall_back() -> None:
    shop = _shop_with(lambda request: httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await shop.search_many(["masło", "cebula"])
    await shop.aclose()


async def test_search_many_caps_concurrency() -> None:
    """The semaphore must bound in-flight requests, whatever the list size."""
    in_flight = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)  # hold the slot so overlap is observable
        in_flight -= 1
        return httpx.Response(200, json=_payload())

    shop = _shop_with(handler, search_concurrency=3)
    await shop.search_many([f"ing-{i}" for i in range(12)])

    assert peak <= 3, f"expected at most 3 concurrent requests, saw {peak}"
    await shop.aclose()


async def test_search_many_dedupes_repeated_ingredients() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["search"])
        return httpx.Response(200, json=_payload(_hit("a", "A")))

    shop = _shop_with(handler)
    result = await shop.search_many(["masło", "masło", "cebula"])

    assert sorted(calls) == ["cebula", "masło"]  # queried once each
    assert set(result) == {"masło", "cebula"}
    await shop.aclose()


async def test_blank_and_empty_queries_short_circuit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not hit the network")

    shop = _shop_with(handler)
    assert await shop.search("   ") == []
    assert await shop.search_many([]) == {}
    await shop.aclose()
