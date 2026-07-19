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
├── base.py       # DeliveryShop ABC + get_shop(id) registry
├── matcher.py    # ProductMatcher (lexical) + ReRanker seam (callable)
├── models.py     # boundary models: Product, ProductMatch, UnmatchedItem, GroceryMatchResult
└── shops/
    └── frisco.py # Frisco provider
```

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
