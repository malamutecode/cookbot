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

## ⚠ Frisco licensing — production traffic is BLOCKED on written consent

Researched 2026-07-25. Not a code problem; do not "fix" it in code.

- **Free, but not permitted.** The search endpoint answers unauthenticated with no
  API key, billing or metering. Cost is not the issue — permission is.
- **Regulamin §8.4** forbids use *"w jakichkolwiek celach a w szczególności
  komercyjnych **bez uprzedniej pisemnej zgody**"*; **§8.3** invokes the Polish
  *Ustawa o ochronie baz danych* (27.07.2001), i.e. *sui generis* database
  protection aimed precisely at systematic extraction. The OpenAPI spec carries no
  `license`, `termsOfService` or `contact` field, so §8 is the only governing text.
  CookBot is commercial multi-tenant SaaS.
- **Not new exposure:** `robots.txt` already scoped the *feed* to "personal,
  non-commercial use", so the older feed-based path is outside those terms too.
- **Operator:** Frisco.pl Sp. z o.o. (KRS 0000261409), 100% Grupa Eurocash S.A.
- **Action:** seek written consent via the
  [partner program](https://www.frisco.pl/stn,program-partnerski). The commercial
  case is favourable (an assistant that fills baskets is affiliate-shaped revenue),
  but they ship a competing assistant (*Friscoach*, Aug 2025) — expect negotiation.

**Dev-scale, read-only work may proceed; production rollout may not.**

### If we ever get consent — verified API facts (don't re-derive)

Probed live 2026-07-25 against `commerce.frisco.pl`; spec at
`/swagger/public/swagger.json` (title `Frisco.Commerce.Web`, 188 paths).

- `GET /api/v1/offer/products/query` → 200 in ~120–150 ms, no auth. **`pageIndex`
  is 1-based** (`0` ⇒ HTTP 400). Hits carry Frisco's own `ordererScore`.
- Search omits `productUrl`/`keywords` (the feed has both), so `Product.url` is
  reconstructed as `https://www.frisco.pl/pid,{id}/stn,product` — the slugless form
  is confirmed 200. Don't try to rebuild the SEO slug.
- `primaryCategory` is the **deepest leaf** (`Malinowe` for "Pomidory malinowe"),
  which drops the searched word; `_search_category()` joins the whole `categories`
  chain instead. This broke a live quality test once — keep the chain.
- No throttling observed at concurrency 4/8/28, and no `Retry-After`. Absence of a
  limit today is not a guarantee — the semaphore stays.
- **Add-to-basket is deliberately unimplemented.** `PUT /api/v1/visitor/cart` is a
  **write** to a third party we have no agreement with. It is a *full-state* PUT
  (replaces the product list, so it needs a GET first), requires an
  `X-Frisco-VisitorId` header, and is postcode-partitioned by `warehouse`. Only the
  GET was probed; the PUT was left untested on purpose. **There is no cart
  merge/transfer endpoint**, so a basket we fill server-side only becomes the
  user's if frisco.pl adopts our `visitorId` client-side — unverified, and the main
  product risk. Logged-in carts use OAuth2 **authorizationCode** (not a password
  grant, so no credential custody), but need Frisco to register a `client_id`.

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

1. Add `shops/{name}.py` with a class satisfying the `DeliveryShop` **Protocol**
   (structural — do not inherit): feed fetch + normalise to `Product`.
2. Register it in `get_shop`.
3. Unit-test the matcher against a fixture feed — no live network in the unit suite.
4. *Optional:* if the shop has its own search backend, also implement
   `search()`/`search_many()` (see `SearchableShop`) — bound the fan-out with a
   semaphore and share one pooled `httpx.AsyncClient`. Test it with an
   `httpx.MockTransport`, as `tests/test_frisco_search.py` does.
