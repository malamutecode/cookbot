# delivery-shops — grocery product matching

> Standalone, **cookbot-independent** library that matches shopping-list
> ingredients to real delivery-shop products. Root context: [CLAUDE.md](../../CLAUDE.md).

## What it is

Given a `ShoppingList` item ("Masło, 200 g"), find the best matching product in a
delivery shop's feed (e.g. Frisco). Clients enable shops by id; **the package owns
the shops, the matching engine, and the boundary models** — clients only configure
a `delivery_shops` list.

```
delivery_shops/
├── base.py       # DeliveryShop + SearchableShop protocols, supports_search(), get_shop(id)
├── matcher.py    # ProductMatcher (lexical) + ReRanker seam (callable)
├── models.py     # boundary models: Product, ProductMatch, UnmatchedItem, GroceryMatchResult
└── shops/
    └── frisco.py # Frisco provider (live search + feed fallback)
```

## Two matching capabilities

A shop must implement `load()`; `search()`/`search_many()` are **optional**.

| | `load()` — catalogue dump | `search_many()` — query |
|---|---|---|
| Contract | every product, matched locally by `ProductMatcher` | the shop ranks, we just map |
| Freshness | as fresh as the feed (Frisco: daily, 12 h TTL) | live prices + stock |
| Cost | Frisco: ~50 MB download + local index per cold start | ~150 ms per ingredient, pooled |
| `generated_at` | the feed's stamp | `None` — results are live |

**Callers must feature-detect** with `supports_search(shop)`, never assume:

```python
from delivery_shops.base import get_shop, supports_search

shop = get_shop("frisco")
if supports_search(shop):
    shortlists = await shop.search_many(ingredients, limit=5)   # dict[query, matches]
else:
    products, generated_at = await shop.load()                   # build a ProductMatcher
```

`search_many` contains per-query failures (a bad ingredient ⇒ empty list) but
**raises when every query fails** — that is the caller's signal to fall back to
`load()`. `clients/tastyhub/app/api/grocery.py` is the reference implementation.

> **Frisco licensing:** the search API is first-party, unauthenticated and free of
> charge, but Frisco's Regulamin §8.4 requires **prior written consent** for
> commercial use and §8.3 invokes Polish database-protection law. See the STEP 50
> blocker note in [TASK.md](../../TASK.md) before production traffic.

## Non-negotiable rules

1. **Shops are providers; clients only configure `delivery_shops`.** Adding a shop
   = a new `DeliveryShop` subclass in `shops/` registered in `get_shop`. Clients
   never subclass or hardcode shop logic — they list shop ids in config.
2. **This package stays LLM-agnostic.** The lexical `ProductMatcher` produces a
   candidate shortlist. When lexical matching is ambiguous ("Masło" vs "Masło z
   czosnkiem"), an optional **`ReRanker` callable seam** disambiguates — but the
   package only defines the seam. The actual LLM re-ranker
   (`ProductReRankAgent`) lives in `cookbot-core` and is injected in; it must
   **not** be imported here. See
   [cookbot-core agents/CLAUDE.md](../cookbot-core/cookbot/agents/CLAUDE.md).
3. **Every boundary is a Pydantic model** (`Product`, `ProductMatch`,
   `UnmatchedItem`, `GroceryMatchResult`) — no raw dicts cross the package edge.

## Adding a shop

1. Add `shops/{name}.py` with a `DeliveryShop` subclass (feed fetch + normalise to `Product`).
2. Register it in `get_shop`.
3. Unit-test the matcher against a fixture feed — no live network in the unit suite.
4. *Optional:* if the shop has its own search backend, also implement
   `search()`/`search_many()` (see `SearchableShop`) — bound the fan-out with a
   semaphore and share one pooled `httpx.AsyncClient`. Test it with an
   `httpx.MockTransport`, as `tests/test_frisco_search.py` does.
