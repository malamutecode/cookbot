# Recipe from a pasted URL → add to calendar

## Context / problem

If the user pastes a recipe link into the chat and says "dodaj ten przepis do
kalendarza", nothing works reliably today. The ChatAgent has 6 tools
(`update_onboarding`, `propose_recipes`, `get_recipe_details`, `add_to_calendar`,
`remove_from_calendar`, `get_shopping_list`) and **none accepts a URL**:

- `get_recipe_details` requires an existing proposal (`_select_proposal(last_proposals, …)`).
  A pasted link is not a proposal, so there is nothing to select. The URL-fetch
  capability exists inside `resolve_recipe` (`build_web_fetch_agent`) but is only
  reachable via a *proposal that already carries a source_url*.
- `add_to_calendar` works on `deps.last_recipe`; with no URL path, that recipe is
  never populated from a link.

So the model improvises — a bare calendar entry with the URL as the name and
empty/hallucinated ingredients, which then breaks the shopping list.

## Goal

Paste a recipe URL → the ChatAgent extracts the real recipe from that page →
shows the recipe card → the user can add it to the calendar (and it flows into the
shopping list) exactly like a normal recipe.

## Design (small — reuses proven extraction)

The verbatim URL extractor already exists and is tested live:
`build_web_fetch_agent` + `web_fetch_prompt(url)` → `Recipe | None` (fetches the
page, extracts ingredients/steps/servings/source_url; never invents content).

### 1. New ChatAgent tool: `get_recipe_from_url(url)`

```python
@agent.tool
async def get_recipe_from_url(ctx, url: str) -> FoundRecipe:
    """Extract the recipe from a URL the user pasted, then show its card."""
```

Behavior (mirrors the known-URL branch of `resolve_recipe`, standalone):
- fetch + extract via the cached `web_fetch` agent, retry once (extraction is
  occasionally flaky); pass `usage=ctx.usage` so tokens aggregate into the turn.
- on success: set `deps.last_recipe = FoundRecipe(recipe, source="web_search")`,
  clear `last_proposals`, append a `FinalRecipeEvent` (the card is shown).
- on empty extraction (page has no readable recipe) or exception: return a
  `source="not_found"` / `source="error"` FoundRecipe and DO NOT emit a card —
  the agent explains conversationally (same contained-failure pattern as the
  other tools, Architecture Rule 7).
- provenance: `source_url` from the page is preserved (Rule 5). We anchor on the
  pasted URL; if the extractor didn't set `source_url`, set it to the pasted URL.

After this tool runs, `add_to_calendar` already works unchanged — it reads
`deps.last_recipe.recipe` for ingredients + attaches the full recipe to the entry,
so the paste → calendar → shopping-list chain is complete with no other changes.

### 2. Instructions

Add to `## Recipe flow`:
> If the user pastes a recipe URL (a link) and asks for that recipe / to add it,
> call `get_recipe_from_url` with the link — do NOT call propose_recipes or run
> onboarding. After it returns, the card is shown; offer to add it to the calendar.

Handle the `not_found`/`error` sources the same way the recipe flow already
documents (explain briefly; no card).

### What does NOT change

- `resolve_recipe`, `propose_recipes`, `get_recipe_details`, `add_to_calendar`,
  the onboarding flow, deps, persistence, WS handler.
- No new sub-agent (reuses `build_web_fetch_agent`).

## Edge cases

- Non-recipe page (article, listing, paywall) → extractor returns None →
  `not_found`; agent says it couldn't read a recipe there.
- URL plus "dla 4 osób" → out of scope for v1 (we extract verbatim; scaling to a
  requested serving count is a separate concern and can be layered later via the
  existing RecipeScaleAgent, exactly like the proposal path).
- Multiple links in one message → tool takes one `url`; the model passes the first
  recipe link. Good enough for v1.
- After extraction, everything is normal free-chat (add to calendar, shopping
  list, ask another).

## Files

- `packages/cookbot-core/cookbot/agents/chat.py` — new `get_recipe_from_url` tool;
  one instruction line; a small `_looks_like_url` guard is not required (the tool
  trusts the model to pass a URL, and a bad string just yields not_found).

## Tests

- **Unit (`tests/test_agents/test_chat.py`)**: with the `web_fetch` sub-agent
  stubbed (patched factory returning a `Recipe`), calling the tool sets
  `last_recipe`, emits a `FinalRecipeEvent`, preserves `source_url`; a stub
  returning `None` yields `source="not_found"` and emits no card.
- **Live (`tests/integration/`)**: paste a known-good recipe URL → tool returns a
  `web_search` recipe with ingredients/steps/source_url; then drive
  `add_to_calendar` and assert a `CalendarAddEvent` whose entry carries the recipe.

## Verification

1. `uv run pytest tests/test_agents/test_chat.py -q` — tool unit tests green.
2. `uv run pytest -m integration -k url` — live paste-URL extraction + calendar add.
3. Manual: paste a kwestiasmaku/aniagotuje recipe link → recipe card appears →
   "dodaj do kalendarza na 10.08" → entry added with real ingredients → shopping
   list from the calendar includes them.
