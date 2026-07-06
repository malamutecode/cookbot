# Direct recipe requests — skip onboarding when the user already knows the dish

## Context / problem

The ChatAgent runs a **guided 5-field onboarding** (`dish_type`, `servings`,
`max_time_minutes`, `ingredients`, `free_notes`) driven by the dynamic
`_onboarding_status` system prompt. It marches the model through the fields one at
a time via `update_onboarding`, and only calls `propose_recipes` once **all five**
are set (`OnboardingState.complete`).

That is right when the user is exploring ("coś na obiad"). It is wrong when the
**first message already names a specific dish**, e.g.:

> "Przepis na halloumi dla 2 osób"

Here the user knows exactly what they want and just needs the recipe. Forcing them
through "ile masz czasu? / jakie składniki? / coś jeszcze?" is friction. The agent
should **recognise the direct request and go straight to `propose_recipes`**,
skipping the remaining questions.

## Goal

When the first user message is a direct recipe request (a concrete dish, optionally
with servings/constraints), the ChatAgent calls `propose_recipes` immediately —
no field-by-field questioning. Exploratory / vague messages keep the current guided
onboarding unchanged.

## Why this is a small, low-risk change

The machinery already exists:

- `propose_recipes(dish_type, ingredients, max_time_minutes=0, servings=2, …)`
  already takes all fields as arguments and **defaults** the optional ones. It does
  not require `OnboardingState.complete`.
- The only thing forcing the 5-question march is the `_onboarding_status` system
  prompt, which currently says "MANDATORY STEPS: call update_onboarding for the
  next missing field, ask ONLY the next question".

So the change is mostly **prompt/instruction logic** plus a tiny bit of state, not
a structural rewrite. No new agents, no new tools. Architecture rules preserved:
onboarding stays guided (not a rigid form), sub-agents stay stateless, the
ChatAgent remains the orchestrator.

## Design

### 1. Distinguish "concrete dish" from "exploratory" intent

`OnboardingState.dish_type` today conflates two things: a real dish name
("halloumi", "makaron carbonara") and the sentinel `"any"` (user said "zaproponuj
coś"). We use that distinction as the signal:

- **Concrete dish** → `dish_type` is a real dish name (not `None`, not `"any"`).
- **Exploratory** → `dish_type` is `None` (not answered) or `"any"`.

Add a helper to `OnboardingState`:

```python
def has_concrete_dish(self) -> bool:
    return bool(self.dish_type) and self.dish_type.strip().lower() not in {"any", ""}
```

### 2. Redefine "ready to search" — dish is enough

The gate to call `propose_recipes` becomes: **either** the full onboarding is
complete (unchanged exploratory path) **or** the user has given a concrete dish.
The other four fields keep their sensible defaults (servings 2, time 0 = no limit,
ingredients [], notes "") — exactly what `propose_recipes` / `to_intent` already
apply.

Add:

```python
def ready_to_search(self) -> bool:
    return self.complete or self.has_concrete_dish()
```

`complete` and `next_missing_field()` stay as-is (still used for the guided path).

### 2b. The decisive signal lives in the tool result (not only the system prompt)

**Implementation note (important).** The dynamic `_onboarding_status` system prompt
is computed once at the *start* of a turn — on the first message the state is still
empty, so it renders the guided block. The model then parses the dish via
`update_onboarding` *within the same turn*, but it's still following the guided
instructions it was given, so it asks the next question instead of searching.

So the direct-request decision is carried by the **`update_onboarding` tool
result**: it now returns `ready_to_search` + a `next_action` string. When a concrete
dish has just been recorded, `next_action` tells the model to call
`propose_recipes` immediately. This is read *after* parsing, in the same turn, so it
actually redirects the model. The system-prompt branch (below) reinforces it on
later turns.

### 3. `_onboarding_status` gains a direct-request branch

Rework the dynamic system prompt so that, **before** emitting the "ONBOARDING IN
PROGRESS / ask the next question" block, it checks `ready_to_search()`:

- If `has_concrete_dish()` and NOT `complete` → emit a **DIRECT RECIPE REQUEST**
  block instructing the model to:
  1. call `update_onboarding` once to record everything it can already parse from
     the message (the dish, and servings/time/ingredients if the user mentioned
     them) — so the values are captured, and
  2. immediately call `propose_recipes` with the collected dish + whatever else is
     known (defaults for the rest). Do **not** ask the remaining questions.
- Else (no concrete dish yet) → the existing guided-onboarding block, unchanged.

The first-message parse already lives in the `update_onboarding` "Parsing rules"
(dish name → dish_type; "zaproponuj"/"cokolwiek" → "any"). We reuse those; the new
branch just changes what happens *after* the dish is known: search now instead of
continuing to ask.

### 4. Main instructions: note the fast path

Add one line to the top-level `## Recipe flow` that a first message naming a
specific dish should go straight to `propose_recipes` (the model already sees the
dynamic prompt, this reinforces it).

### What does NOT change

- `update_onboarding`, `propose_recipes`, `get_recipe_details` signatures/bodies.
- The guided onboarding for vague requests.
- `ChatAgentDeps`, `reset_turn`, persistence, WS handler.
- Free-chat mode after the first recipe.

## Edge cases

- **"Przepis na halloumi dla 2 osób"** → dish_type="halloumi", servings=2 parsed in
  one `update_onboarding`; then `propose_recipes` immediately. Time/ingredients/
  notes default. ✅ the target case.
- **"halloumi"** (bare dish, no servings) → still a concrete dish → search with
  servings=2 default. ✅
- **"zaproponuj coś na szybko"** → dish_type="any" (not concrete) → guided
  onboarding continues asking. ✅ unchanged.
- **"coś z kurczaka i ryżu"** — no concrete *dish*, but ingredients given. This is
  exploratory (dish_type would be "any"); guided path applies. Not a regression;
  can be revisited later if we want ingredient-only fast paths.
- After the first recipe, everything is free-chat as before; the fast path only
  matters on the initial request while onboarding is incomplete.

## Files

- `packages/cookbot-core/cookbot/agents/chat.py`
  - `OnboardingState`: add `has_concrete_dish()` + `ready_to_search()`.
  - `_onboarding_status`: add the direct-request branch before the guided block.
  - main `instructions`: one line noting the fast path.

## Tests

- **Unit (`tests/test_agents/test_chat.py`)**:
  - `OnboardingState` helpers: concrete dish vs "any"/None; `ready_to_search`.
  - Prompt guard: with a concrete dish + incomplete state, `_onboarding_status`
    output contains the direct-request instruction (goes to propose_recipes) and
    NOT the "ask the next question" march.
- **Live (`tests/integration/test_chat_e2e_live.py` or a new direct-recipe live
  test)**: send "Przepis na halloumi dla 2 osób" as the first message; assert the
  turn results in a `recipe_options` event (proposals shown) WITHOUT the agent
  first asking about time/ingredients. Assert servings=2 captured.

## Verification

1. `cd packages/cookbot-core && uv run pytest tests/test_agents/test_chat.py -q`
   — helper + prompt-guard tests green.
2. `uv run pytest -m integration -k direct -q` — live: direct request → proposals,
   no extra questions.
3. Manual: open the widget, first message "Przepis na halloumi dla 2 osób" → 4
   recipe cards appear immediately; "coś na obiad" → still asks the guided
   questions.
