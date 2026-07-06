# Requirements — Manual shopping list: add, organize, export

## Context / problem

The **Lista zakupów** (shopping list) panel today is *read-only from the user's
point of view*: items only arrive by exporting from the calendar or via the chat
agent. The user cannot:

- add their own items by hand (and they may want to add *anything* — not just
  cooking ingredients: "baterie AA", "worki na śmieci", "znaczki pocztowe");
- tidy a messy free-text list (merge duplicates, sum amounts, assign shop
  sections) on demand;
- get the list out of the app to send to someone (mail / Messenger).

The list also does not survive a page reload (unlike the calendar), so a
hand-built list is lost on refresh.

## Goal

Make the shopping list a **first-class, user-editable list** that the user can
build by hand, one-click **organize** (merge + categorize) via the existing
ShoppingListAgent, and **export** as clean text to paste anywhere.

## Functional requirements

### FR1 — Add items as free text
- An input field + "Dodaj" button (and Enter key) at the top/bottom of the list.
- Accepts arbitrary text — one item per submit. It need **not** be a cooking
  ingredient (e.g. "baterie AA", "prezent dla mamy"). No validation beyond
  non-empty / trimmed.
- New items are appended unchecked, with **no section** (they show in the flat
  list until the list is organized). Adding an item never triggers a network call.

### FR2 — Edit / remove items
- Each item can be removed (existing "Wyczyść zaznaczone" already removes checked
  items; additionally allow removing a single row).
- Checkbox toggle behavior stays as-is.

### FR3 — Organize the list ("Poukładaj listę zakupów")
- A button "Poukładaj listę zakupów" sends the current item names to the existing
  `POST /v1/shopping-list/build` endpoint (ShoppingListAgent).
- The agent (already built) **merges identical items and sums amounts**, assigns a
  **section**, and normalizes units (via the measure tool added earlier). Result
  replaces the current list, now grouped by section.
- Checked state is discarded on reorganize (the list is rewritten). Show a loading
  state while it runs; on failure, leave the list unchanged.
- Non-cooking items must survive: the agent puts anything it can't categorize into
  the **"inne"** section rather than dropping it. (Covered by an agent-prompt note
  + a live test.)

### FR4 — Export / share (two separate buttons)
- **"Kopiuj"** — builds a clean **plain-text** rendering of the list (grouped by
  section when present, else a flat bulleted list, with a heading) and copies it to
  the clipboard (`navigator.clipboard.writeText`), showing a brief "Skopiowano ✓".
- **"Udostępnij"** — opens the native OS share sheet via `navigator.share({ text })`
  (mobile / supported browsers) so the list can go straight to Messenger / mail.
  Shown only when `navigator.share` exists (mostly mobile); hidden on desktop.
- Neither mutates the list.

### FR6 — Supermarket-aisle categories
- The organizer assigns each item to a **store aisle**, not just cooking sections,
  so the shopper can look at one aisle at a time: warzywa/owoce, nabiał i jaja,
  mięso/ryby/wędliny, pieczywo, mrożonki, produkty suche/sypkie, napoje,
  słodycze/przekąski, chemia/dom, higiena/kosmetyki, inne.
- Household/hygiene/drink items get a real aisle (papier toaletowy → chemia/dom),
  and produce like czosnek/cebula goes to warzywa/owoce — never "inne".
- The taxonomy lives once in `SECTIONS` (backend) and is mirrored by the frontend
  `SECTION_ORDER` for display ordering.

### FR5 — Persistence
- The shopping list persists to **localStorage** (key `tastyhub_shopping`), matching
  how the calendar persists (`tastyhub_calendar`). It survives reload. It is
  per-browser (no server sync in this iteration).

## Non-functional / constraints

- **Frontend-only where possible.** Reuse the existing `POST /v1/shopping-list/build`
  endpoint and `ShoppingListAgent` for FR3 — no new backend endpoint. The only
  backend change is a small prompt note so non-cooking items land in "inne"
  (FR3), with a test.
- Keep the widget's existing visual style (the red `#c0392b` accent, section
  grouping already in `ShoppingList.tsx`).
- Text export must be paste-safe plain text (no markdown/HTML), because the target
  is arbitrary mail/messenger inputs.
- TypeScript must typecheck (`tsc --noEmit`); no test runner exists for the
  frontend, so verification is `tsc` + manual/agent-driven UI check. Backend prompt
  change gets a unit prompt-guard test and (optionally) a live test.

## Out of scope (this iteration)

- Server-side (Firestore) sync across devices.
- Per-item quantity editing UI (amounts come from the organize step / free text).
- Reordering by drag. Undo.

## Affected files

- `frontend/src/components/ShoppingList.tsx` — add input, organize button, export
  button, single-row remove.
- `frontend/src/App.tsx` — localStorage persistence for `shopItems`
  (load/save like `calDays`).
- `frontend/src/types.ts` + `packages/cookbot-core/cookbot/models/ui_strings.py`
  + tenant defaults — new UI strings (add placeholder/button, organize, export,
  copied-confirmation).
- `packages/cookbot-core/cookbot/agents/shopping_list.py` — prompt note: keep
  non-food items, category "inne". Test in `test_shopping_list.py`.

## Acceptance / verification

1. Type "baterie AA" + Enter → appears in the list; reload page → still there
   (localStorage).
2. Add "2 cebule", "1 cebula", "mleko" → click **Poukładaj listę zakupów** →
   cebula merged with a summed amount, items grouped by section, "baterie AA" ends
   up under "inne".
3. Click **Kopiuj/Udostępnij** → clipboard contains a clean grouped text list;
   "Skopiowano ✓" shows; on mobile the share sheet opens.
4. `cd frontend && npx tsc --noEmit` clean.
5. Backend: `uv run pytest tests/test_agents/test_shopping_list.py` green (prompt
   guard incl. the "inne" rule).
