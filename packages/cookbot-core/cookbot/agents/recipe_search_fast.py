"""Zero-LLM fast path for plain recipe requests (STEP 47).

When the user just names a dish with no extra requirements ("znajdź przepis na
jagodzianki"), there is nothing for a model to reason about: we need recipe pages
for that dish. The RecipeOptionsAgent's expensive part is not the search — it is
the SECOND LLM call that *writes* four proposals (description, difficulty, time,
key ingredients) as generated prose. This module removes that call entirely.

Everything here is deterministic Python:

  DDG search  →  rank/filter URLs  →  scrape each page's <head>  →  RecipeSummary

`enrich_from_page_head` reads the same first bytes that `populate_proposal_images`
already fetches for og:image, so og:description and schema.org/Recipe JSON-LD are
effectively free — no extra request, no extra latency. Where a page ships JSON-LD
the metadata chips fill in with REAL page data; where it does not they stay empty
and the frontend hides them.

**Never add an Agent to this module.** A model asked to fill in a missing cooking
time will invent one, which is exactly the fabrication the verbatim-extraction
rule (agents/CLAUDE.md, Hard Rule 5) exists to prevent. Empty is honest; invented
is not.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from cookbot.models.recipe import RecipeSummary

if TYPE_CHECKING:
    from cookbot.agents.chat import OnboardingState

log = structlog.get_logger()

# DDG is asked for more than we show: ranking drops forums/tags/listicles and
# de-duplicates by domain, so the raw list must be able to absorb those losses.
_DDG_MAX_RESULTS = 20

_HEAD_FETCH_TIMEOUT = 3.0     # per page; metadata is nice-to-have, fail fast
# Ceiling for the whole concurrent enrichment stage. Measured: DDG search alone is
# a ~1.9-2.8s floor, and enrichment ranged 0.19-2.77s depending on whether a slow
# host was in the result set. Capping the stage keeps the tail predictable — a
# card without its photo still works, a 6s wait does not.
_ENRICH_TOTAL_BUDGET_SECONDS = 2.5
_HEAD_MAX_BYTES = 120_000     # og:* lives in <head>; JSON-LD sometimes trails it
_HEAD_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# A dedicated recipe page is what we want. These path shapes are the strongest
# available signal for one, and they are cheap to check.
_RECIPE_URL_HINTS = ("/przepis/", "/przepisy/", "/recipe/", "/recipes/")

# Ported from the RecipeOptionsAgent prompt (recipe_options.py), where these
# rules previously existed ONLY as prose for the model to follow. Forums, tag and
# category listings, search pages and magazine listicles are never a single
# recipe with an ingredient list.
_BLOCKED_URL_RE = re.compile(
    r"(/forum|/tag/|/tagi/|/kategoria/|/kategorie/|/category/|/search|/szukaj"
    r"|/temat/|/watek/|/thread|/comment|/author/|/autor/"
    r"|ofeminin\.pl|wp\.pl/artykul|onet\.pl|interia\.pl|pinterest\.|facebook\.|youtube\.)",
    re.IGNORECASE,
)

# Listicle/round-up titles ("Top 10 ciast", "5 przepisów na...") are articles
# about recipes, not recipes.
_LISTICLE_TITLE_RE = re.compile(r"^\s*(top\s*\d+|\d+\s+(przepis|najlep|pomys|sposob|sposób))", re.IGNORECASE)

_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE
)
_OG_DESC_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:description["\'][^>]+content=["\']([^"\']*)["\']', re.IGNORECASE
)
_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL
)
# ISO-8601 duration as used by schema.org recipeYield/totalTime ("PT1H30M").
_ISO_DURATION_RE = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?$", re.IGNORECASE)

_CONSTRAINT_KEYWORD_RE = re.compile(
    r"\b(bez|szybk|light|wegan|wegetar|bezglutenow|dietetyczn|niskokalor|fit|tani|prost|latwy|łatwy"
    r"|w\s+\d+\s*min|do\s+\d+\s*min|kcal|keto)", re.IGNORECASE
)

_MAX_DESCRIPTION_CHARS = 220


@dataclass
class PageMeta:
    """What a page's <head> yields for free, alongside the og:image we already fetch."""
    image_url: str | None = None
    description: str | None = None
    total_time_minutes: int = 0
    key_ingredients: list[str] = field(default_factory=list)


# ── Trigger predicate ─────────────────────────────────────────────────────────

def is_fast_path_request(ob: OnboardingState, message: str) -> bool:
    """True when the request is a plain "find me a recipe for X" with no extras.

    Two independent gates, both required:

    1. `OnboardingState` carries a concrete dish and no constraint fields.
    2. The raw user message carries no constraint keywords. This second check
       matters because the state is populated by the same model call the fast
       path is trying to make cheap — trusting it alone would be circular.

    `servings` is deliberately NOT a constraint: it changes what the recipe is
    scaled to after the pick, not which pages are worth showing.
    """
    if not ob.has_concrete_dish():
        return False
    if ob.max_time_minutes or ob.ingredients or ob.free_notes:
        return False
    # An empty message means we cannot verify gate 2, so we do not take the
    # shortcut. Every real turn sets current_user_message before the run.
    if not (message or "").strip():
        return False
    return not _CONSTRAINT_KEYWORD_RE.search(message)


def fast_path_query(ob: OnboardingState, site_filter: str = "") -> str:
    """DDG query for a plain dish request.

    "przepis" is appended because the bare dish name alone surfaces Wikipedia and
    shop listings. The site filter is left unparenthesised — `(site:a OR site:b)`
    is unreliable on DDG (same rationale as recipe_options_prompt).
    """
    dish = (ob.dish_type or "").strip()
    query = f"{dish} przepis" if "przepis" not in dish.lower() else dish
    return f"{query} {site_filter}".strip() if site_filter else query


# ── URL ranking (pure — unit-tested without network) ──────────────────────────

def _domain(url: str) -> str:
    parts = url.split("/", 3)
    return parts[2].lower().removeprefix("www.") if len(parts) > 2 else ""


def _is_bare_homepage(url: str) -> bool:
    """"https://site.test/" or "https://site.test" — a homepage, never a recipe."""
    after_scheme = url.split("://", 1)[-1]
    return after_scheme.rstrip("/").count("/") == 0


def _score(result: dict[str, Any]) -> int:
    """Higher is better. Only used for ordering, never exposed."""
    url = result.get("href", "").lower()
    score = 0
    if any(hint in url for hint in _RECIPE_URL_HINTS):
        score += 10
    # A deep path is more likely a specific page than a shallow section index.
    score += min(url.rstrip("/").count("/") - 2, 3)
    return score


def rank_recipe_results(results: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Filter out non-recipe pages, prefer recipe-shaped URLs, one card per domain.

    Domain de-duplication is deliberate: six cards from six sites give the user a
    real choice, six from one site do not.
    """
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for i, r in enumerate(results):
        url = (r.get("href") or "").strip()
        if not url or _is_bare_homepage(url) or _BLOCKED_URL_RE.search(url):
            continue
        if _LISTICLE_TITLE_RE.search(r.get("title") or ""):
            continue
        # i keeps DDG's own relevance order as the tie-break within a score band.
        scored.append((-_score(r), i, r))

    scored.sort(key=lambda t: (t[0], t[1]))

    picked: list[dict[str, Any]] = []
    seen_domains: set[str] = set()
    for _, _, r in scored:
        d = _domain(r["href"])
        if d in seen_domains:
            continue
        seen_domains.add(d)
        picked.append(r)
        if len(picked) >= limit:
            break
    return picked


# ── Page <head> metadata (free — same bytes as the og:image fetch) ────────────

def _iso_duration_to_minutes(value: str) -> int:
    m = _ISO_DURATION_RE.match(value.strip())
    if not m:
        return 0
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    return hours * 60 + minutes


def _find_recipe_node(data: Any) -> dict[str, Any] | None:
    """Locate a schema.org/Recipe node inside a JSON-LD blob.

    Real pages wrap it in @graph, in a list, or nest it — so this walks."""
    if isinstance(data, list):
        for item in data:
            if (found := _find_recipe_node(item)) is not None:
                return found
        return None
    if not isinstance(data, dict):
        return None
    node_type = data.get("@type")
    types = node_type if isinstance(node_type, list) else [node_type]
    if any(isinstance(t, str) and t.lower() == "recipe" for t in types):
        return data
    if "@graph" in data:
        return _find_recipe_node(data["@graph"])
    return None


def _parse_jsonld_recipe(page_html: str) -> tuple[int, list[str]]:
    """(total_time_minutes, key_ingredients) from schema.org/Recipe, if present.

    The parameter is NOT named `html` — that shadows the stdlib module used for
    entity decoding a few lines down."""
    for match in _JSONLD_RE.finditer(page_html):
        try:
            data = json.loads(match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            continue
        node = _find_recipe_node(data)
        if node is None:
            continue
        minutes = 0
        for key in ("totalTime", "cookTime", "prepTime"):
            raw = node.get(key)
            if isinstance(raw, str) and (minutes := _iso_duration_to_minutes(raw)):
                break
        raw_ingredients = node.get("recipeIngredient") or node.get("ingredients") or []
        ingredients = [html.unescape(str(i).strip()) for i in raw_ingredients if str(i).strip()][:5]
        return minutes, ingredients
    return 0, []


async def enrich_from_page_head(client: httpx.AsyncClient, url: str) -> PageMeta | None:
    """Best-effort scrape of og:image, og:description and JSON-LD from one page.

    Returns None on any failure — metadata is a bonus, never a reason to lose a
    card.

    Streams and stops at `_HEAD_MAX_BYTES` instead of `await client.get(...).text`.
    A plain GET waits for the WHOLE body before we can slice it, and recipe pages
    are big: mojegotowanie.pl is 158 kB and beszamel.se.pl 125 kB, while the
    metadata we want sits at offsets 1.6k-17.5k. Under a 6s budget across six
    concurrent pages that full download is what makes cards lose their image —
    measured live as image coverage dropping from 5/6 to 3/6. Aborting early both
    fixes the coverage and cuts the transfer.
    """
    try:
        async with client.stream("GET", url, headers=_HEAD_FETCH_HEADERS) as resp:
            if resp.status_code != 200:
                return None            # 403 bot-blocks are common; not an error worth logging
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)
                total += len(chunk)
                if total >= _HEAD_MAX_BYTES:
                    break
            encoding = resp.encoding or "utf-8"
        head = b"".join(chunks).decode(encoding, errors="replace")
    except Exception:
        return None

    img = _OG_IMAGE_RE.search(head)
    desc = _OG_DESC_RE.search(head)
    minutes, ingredients = _parse_jsonld_recipe(head)
    return PageMeta(
        # Both arrive entity-encoded. `&amp;` in an og:image query string breaks
        # the <img src>; `&#243;` in the description renders literally on the card.
        image_url=html.unescape(img.group(1)) if img else None,
        description=html.unescape(desc.group(1).strip()) if desc and desc.group(1).strip() else None,
        total_time_minutes=minutes,
        key_ingredients=ingredients,
    )


# ── Search + proposal construction ────────────────────────────────────────────

def _ddg_search(query: str, max_results: int) -> list[dict[str, Any]]:
    """Blocking DDG text search. Wrapped in a thread by the caller (Rule 4).

    Imported lazily so the module stays importable (and unit-testable) without
    the optional `ddgs` dependency present.
    """
    try:
        from ddgs.ddgs import DDGS
    except ImportError:  # legacy package name
        from duckduckgo_search import DDGS  # type: ignore[no-redef]
    return list(DDGS().text(query, max_results=max_results))


def _clip(text: str) -> str:
    """Normalise whitespace, decode HTML entities, and clip to card length.

    Entity decoding is load-bearing, not cosmetic: DDG snippets and og:description
    both arrive entity-encoded, so without it cards render literally as
    "Ponad 10 najlepszych przepis&#243;w na żurek" (observed live on "żurek").
    """
    text = html.unescape(" ".join((text or "").split()))
    if len(text) <= _MAX_DESCRIPTION_CHARS:
        return text
    return text[:_MAX_DESCRIPTION_CHARS].rsplit(" ", 1)[0] + "…"


async def build_fast_proposals(query: str, limit: int) -> list[RecipeSummary]:
    """Search, rank, and build up to `limit` proposals — with NO model call.

    Contains its own failures (Hard Rule 7): any error returns an empty list, and
    the caller falls back to the RecipeOptionsAgent.
    """
    try:
        results = await asyncio.to_thread(_ddg_search, query, _DDG_MAX_RESULTS)
    except Exception as exc:
        log.warning("fast_path_search_failed", query=query, error=str(exc))
        return []

    ranked = rank_recipe_results(results, limit=limit)
    if not ranked:
        return []

    proposals = [
        RecipeSummary(
            name=_clip(r.get("title") or "").strip() or "Przepis",
            description=_clip(r.get("body") or ""),
            difficulty="",             # never invented — the card hides empty chips
            total_time_minutes=0,      # filled from JSON-LD below when the page has it
            key_ingredients=[],
            source="web_search",
            source_url=r["href"],
            image_url=None,
        )
        for r in ranked
    ]

    # Enrichment is capped as a WHOLE, not just per-request. The per-page timeout
    # bounds one slow site, but six pages fetched concurrently are still bounded by
    # the slowest of them, and a single stalling host was measured pushing the turn
    # from ~2.9s to 6.2s. Cards are already complete without metadata, so when the
    # budget runs out we ship what arrived rather than make the user wait.
    metas: list[Any] = [None] * len(proposals)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=_HEAD_FETCH_TIMEOUT) as client:
            metas = await asyncio.wait_for(
                asyncio.gather(
                    *(enrich_from_page_head(client, p.source_url or "") for p in proposals),
                    return_exceptions=True,
                ),
                timeout=_ENRICH_TOTAL_BUDGET_SECONDS,
            )
    except TimeoutError:
        log.warning("fast_path_enrich_timeout", query=query, budget=_ENRICH_TOTAL_BUDGET_SECONDS)

    for p, meta in zip(proposals, metas):
        if not isinstance(meta, PageMeta):
            continue
        if meta.image_url:
            p.image_url = meta.image_url
        if meta.description:
            # The site author's own summary beats a DDG SEO snippet.
            p.description = _clip(meta.description)
        if meta.total_time_minutes:
            p.total_time_minutes = meta.total_time_minutes
        if meta.key_ingredients:
            p.key_ingredients = meta.key_ingredients

    log.info(
        "fast_path_proposals_built",
        query=query,
        raw_results=len(results),
        ranked=len(ranked),
        with_image=sum(1 for p in proposals if p.image_url),
        with_metadata=sum(1 for p in proposals if p.total_time_minutes or p.key_ingredients),
    )
    return proposals
