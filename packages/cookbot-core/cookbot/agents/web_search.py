import re

import httpx
import structlog
from markdownify import markdownify as md
from pydantic_ai import Agent
from pydantic_ai._ssrf import safe_download
from pydantic_ai.common_tools.duckduckgo import duckduckgo_search_tool
from pydantic_ai.common_tools.web_fetch import WebFetchLocalTool, WebFetchResult
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import BinaryContent
from pydantic_ai.tools import Tool

from cookbot.models.recipe import (
    ParsedIngredients,
    Recipe,
    UserIntent,
    sanitize_servings,
)
from cookbot.models.tenant import TenantConfig

log = structlog.get_logger()

_WEB_SEARCH_MAX_RESULTS = 5
_FETCH_TIMEOUT_SECONDS = 30
# Recipe pages carry heavy nav/menu/related-recipe markup BEFORE the recipe.
# Measured: kwestiasmaku.com "makaron ze szpinakiem" fetches to ~40k chars of
# markdown, and the ingredient list ("Składniki", incl. cebula + the 150 g
# makaron) doesn't start until ~25.8k. The old 24k cap truncated it away, so the
# model never saw the real ingredients — gpt-4o then (correctly) returned null,
# while gpt-4o-mini fabricated a plausible-but-wrong recipe (missing cebula,
# inflated makaron). The cap MUST clear a full recipe body. 48k covers observed
# pages with headroom; ~16k tokens is well within model context and TPM limits.
# If a page is still truncated, raise this rather than accept a fabricated recipe.
_MAX_PAGE_CONTENT = 48_000

# markdownify's `strip=[...]` removes the TAGS but keeps their TEXT, so inline
# <style>/<script> bodies survive as raw CSS/JS in the markdown. On WordPress
# blogs that is enormous: chilitonka.com's curry post converts to ~238k chars of
# markdown, of which a single inline stylesheet is ~25k and a GlotPress
# translation blob ~69k. The recipe ("Składniki dla 4 osób") only began at char
# ~68.5k — far past _MAX_PAGE_CONTENT, so the fetch truncated the ENTIRE recipe
# away and the extractor saw nothing but boilerplate.
#
# Removing these elements (content included) before the markdown conversion cuts
# that page to ~82k chars and moves the ingredient list to ~5.1k, comfortably
# inside the cap. This is a pre-processing fix, not a bigger cap: raising the cap
# alone would just spend tokens on minified CSS.
_HTML_NOISE_RE = re.compile(
    r"<(script|style|noscript|svg|template|iframe)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_EXCESS_NEWLINES_RE = re.compile(r"\n{3,}")


def _strip_html_noise(html: str) -> str:
    """Drop <script>/<style>/etc. elements *including their text content*."""
    return _HTML_NOISE_RE.sub(" ", html)


async def _fetch_markdown(
    url: str,
    max_content_length: int = _MAX_PAGE_CONTENT,
    *,
    pinned_url: str | None = None,
) -> WebFetchResult | BinaryContent:
    """Shared fetch pipeline: SSRF-safe download → strip noise → markdown → cap.

    The single implementation behind both `recipe_web_fetch_tool` (the agent-facing
    Tool) and `fetch_page_markdown` (the plain coroutine). Kept in one place so the
    split cross-check reads exactly the text the extractor read — two fetch paths
    would eventually diverge and make the cross-check compare against a different
    document than the one the model saw.
    """
    if pinned_url:
        url = pinned_url
    try:
        response = await safe_download(
            url,
            allow_local=False,
            timeout=_FETCH_TIMEOUT_SECONDS,
            headers={"Accept": "text/markdown, text/html;q=0.9, */*;q=0.8"},
        )
    except (ValueError, httpx.HTTPStatusError, httpx.RequestError) as e:
        raise ModelRetry(f"Failed to fetch {url}: {e}") from e

    media_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type not in ("", "text/html", "application/xhtml+xml"):
        # JSON / markdown / binary: no noise problem — use the stock behaviour.
        stock = WebFetchLocalTool(
            max_content_length=max_content_length,
            allow_local_urls=False,
            timeout=_FETCH_TIMEOUT_SECONDS,
        )
        return await stock(url)

    html = response.text
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else ""

    content = md(_strip_html_noise(html), strip=["img", "script", "style"])
    content = _EXCESS_NEWLINES_RE.sub("\n\n", content).strip()
    if len(content) > max_content_length:
        content = content[:max_content_length]

    return WebFetchResult(url=str(response.url), title=title, content=content)


async def fetch_page_markdown(url: str, max_content_length: int = _MAX_PAGE_CONTENT) -> str:
    """Fetch a page as cleaned markdown — no LLM, no Tool wrapper.

    The same pipeline `recipe_web_fetch_tool` runs (noise-stripped HTML → markdown
    → cap), exposed as a plain coroutine so non-agent callers can use it without
    going through PydanticAI's `Tool`, whose `.function` is typed as possibly
    ctx-taking. Used by the STEP 45 split cross-check, which needs the text the
    extractor saw but has no model call to make.
    """
    # WebFetchResult is a TypedDict (hence dict access, not attributes); a
    # BinaryContent response has no markdown to offer, so it yields "".
    result = await _fetch_markdown(url, max_content_length, pinned_url=url)
    if isinstance(result, dict):
        return str(result.get("content", ""))
    return ""


def recipe_web_fetch_tool(
    max_content_length: int = _MAX_PAGE_CONTENT,
    pinned_url: str | None = None,
) -> Tool[None]:
    """`web_fetch` that strips script/style noise BEFORE markdown conversion.

    Same contract as PydanticAI's `web_fetch_tool` (and the same SSRF-protected
    downloader), but for HTML we clean the source before converting to markdown,
    then truncate — so the character budget is spent on the recipe rather than on
    inline CSS/JS. Non-HTML responses fall through to the stock tool.

    `pinned_url` forces every fetch to that exact URL, ignoring the model's
    argument. Tool arguments are GENERATED TEXT: asked to fetch a long slug the
    model retypes it and drops characters — observed live on the chilitonka curry
    post as ".../chlebkiem-naan/" → ".../chlebkiem-na-nan/", a 404 that made
    extraction return null and told the user the page had no recipe. When the
    caller already knows the exact URL there is nothing for the model to decide,
    so we pin it rather than hope the retype is faithful.
    """
    async def web_fetch(url: str) -> WebFetchResult | BinaryContent:
        """Fetch the content of a web page at the given URL and return it as markdown."""
        return await _fetch_markdown(url, max_content_length, pinned_url=pinned_url)

    return Tool(web_fetch, name="web_fetch")

_EXTRACT_INSTRUCTIONS = """
## Steps
1. **Fetch the page**: call `web_fetch` with the URL provided in the prompt.
2. **Read the WHOLE markdown**, then **extract the COMPLETE recipe VERBATIM**:
   - name, description
   - ingredients: EVERY item from the ingredient list, with the EXACT quantities
     as written on the page — do NOT recalculate, round, or scale them. Copy each
     line as-is. A real recipe has several ingredients — if you captured only 1-2,
     you missed the list; scan the page again. Include items under any sub-headers
     (e.g. "sos", "do podania") — and when you find such sub-headers, they also
     each need an entry in `components` (step 3). Common items are easy to overlook:
     onion (cebula), garlic (czosnek), salt, pepper, oil/butter — check none are missing.
     PRESERVE EVERY PRODUCT QUALIFIER EXACTLY — the specific product form is part of
     the ingredient and must never be dropped or generalised:
       * fat / percentage: "śmietanka 30%" is NOT "śmietana"; "mleko 3,2%" is not "mleko".
       * product type / suffix: "śmietanka" (cream for cooking) ≠ "śmietana" (soured
         cream); "masło extra" ≠ "masło"; keep "-ka"/"-ta" and words like "tłusta",
         "kwaśna", "gęsta", "light".
       * variety / cut / size: "papryka czerwona", "cukier trzcinowy",
         "pierś z kurczaka", "duże jajko" — keep the qualifier word.
     Copy the ingredient's full wording verbatim; never simplify a specific product
     to its generic name.
   - steps: ALL preparation steps in order, as written. A real recipe has multiple
     steps — one step is almost always wrong; re-scan for the full method.
   - prep_time_minutes, cook_time_minutes (integers)
   - difficulty: exactly "Easy", "Medium", or "Hard"
   - servings: the NUMBER OF PORTIONS stated on the page (e.g. "porcje: 2" → 2).
     Do not guess or change it. If the page gives none, use 0.
     A number with a UNIT is a YIELD, not a portion count — recipe sites reuse the
     "Liczba porcji" label for the batch weight or volume. Use 0 for these:
       * "Liczba porcji: 2000g", "Porcje: 1,5 kg", "Liczba porcji: 500 ml" → 0
       * "Porcje: 1 blacha", "Liczba porcji: 1 forma", "na 1 tortownicę" → 0
     A bare number, or one followed by a portion word, IS a count:
       * "Liczba porcji: 4", "Porcje: 6 osób", "4 porcje" → 4, 6, 4
     For a range ("4-6 porcji") take the FIRST number (→ 4). Never report a
     serving count above 100 — if the page's number is larger, it is a yield or a
     typo, so use 0.
   - tips: practical tips from the page (empty list if none)
   - source_url: the URL you fetched (copy exactly)
   - image_url: og:image URL if visible in the markdown; otherwise null
   - components: see step 3 — this is REQUIRED whenever the page has more than
     one ingredient heading.
3. **Count the ingredient headings on the page, then fill `components`.**
   Look for every separate ingredient list — headings like "Składniki",
   "Składniki dla 4 osób", "Składniki na 8 porcji", "Sos", "Ciasto", "Krem", or a
   named dish followed by its own list. Ask yourself literally: *how many separate
   ingredient lists does this page have?*
   - Exactly ONE → `components` MUST be an empty list `[]`. This is the usual case.
   - TWO OR MORE → `components` MUST have ONE ENTRY PER LIST, in page order, with
     the FIRST entry being the main recipe. Never return fewer entries than the
     number of ingredient headings you found. For each block copy:
       * name: the block's heading, or the dish it belongs to, as written on the
         page ("Chlebek naan", "Sos czosnkowy", "Ciasto")
       * servings: the count stated FOR THAT BLOCK — "Składniki na 8 porcji" → 8,
         "Składniki dla 4 osób" → 4; use 0 when that block states none, and also
         when its number carries a unit ("na 500 g ciasta" is a yield, not a count)
       * ingredients / steps: that block's own items, verbatim
     Yes, this repeats lines already in the main `ingredients` list. That is
     intended — copy them again rather than leaving a block out.
   Just REPORT what the page contains. Do NOT decide whether the blocks are one
   dish or two, and do NOT drop a block because it looks like a component — that
   judgement happens elsewhere.
   Worked example — a page titled "Curry z chlebkiem naan" with "Składniki dla
   4 osób" (curry) and "Składniki na 8 porcji" (naan) has TWO ingredient
   headings, so `components` has exactly 2 entries: the curry (servings=4) and
   the naan (servings=8).
4. Only return null if the page genuinely has NO recipe (a 404 page, a paywall,
   a category/listing page, or an unrelated article). A normal recipe page —
   even one buried in lots of navigation, ads, or comments — must be extracted.
   Recipe sites often place ingredients and steps far down the markdown; read the
   whole page before concluding there is no recipe.

## Rules
- NEVER invent ingredients or steps — extract only what is on the page.
- NEVER scale, convert, or adjust quantities or servings. Faithful extraction only;
  quantities and the serving count must match the page exactly. Adjusting for a
  different number of people is a SEPARATE step handled elsewhere, not your job.
- Do NOT stop early: capture the full ingredient list and every step.
- Always set source_url to the exact URL fetched.
- `ingredients` and `steps` must ALWAYS carry the whole page, even when you also
  fill `components`. The two are not alternatives: components is extra reporting
  ON TOP of the complete extraction, never a replacement for part of it.
"""

_SEARCH_INSTRUCTIONS = """
## Steps
1. **Search**: use `duckduckgo_search` with the query provided verbatim.
2. **Pick the best URL**: choose the result most likely to be a real recipe page.
   Prefer domains mentioned in the query (site: operators).
   Avoid aggregator homepages, forum threads, or listicles.
3. **Fetch the page**: call `web_fetch` with that URL.
4. **Extract the recipe VERBATIM** from the fetched markdown:
   - name, description
   - ingredients: EVERY item, with the EXACT quantities as written — do NOT scale
     or recalculate. Common items are easy to miss: onion, garlic, salt, oil.
     PRESERVE EVERY PRODUCT QUALIFIER EXACTLY — never generalise a specific product
     to its generic name: "śmietanka 30%" ≠ "śmietana", "mleko 3,2%" ≠ "mleko",
     "masło extra" ≠ "masło". Keep fat %, type suffixes (-ka/-ta), and variety
     words (czerwona, trzcinowy, tłusta) verbatim.
   - steps: numbered, actionable, as written on the page
   - prep_time_minutes, cook_time_minutes (integers)
   - difficulty: exactly "Easy", "Medium", or "Hard"
   - servings: the NUMBER OF PORTIONS stated on the page (0 if none given); do not
     change it. A number with a UNIT is a YIELD, not a count — "Liczba porcji:
     2000g", "Porcje: 1,5 kg", "Porcje: 1 blacha" all mean the page states no
     portion count, so use 0. A bare number or one with a portion word ("Porcje: 4",
     "6 osób") is a real count. For a range ("4-6") take the first number. Never
     report a count above 100 — that is a yield or a typo, so use 0.
   - tips: practical tips from the page (empty list if none)
   - source_url: the URL you fetched (copy exactly)
   - image_url: og:image URL if visible in the markdown; otherwise null
   - components: count the separate ingredient headings on the page. Exactly ONE
     → `[]` (the usual case). TWO OR MORE (e.g. "Składniki dla 4 osób" for the
     main dish and "Składniki na 8 porcji" for a bread) → one entry per list, in
     page order, main recipe FIRST, each with its own name / servings /
     ingredients / steps copied verbatim. Repeating lines from the main
     `ingredients` list is intended. Report only; never judge or drop a block.
5. If the page has no real recipe, try the second-best URL from step 1.
   If none work, return null.

## Rules
- NEVER invent ingredients or steps — extract only what is on the page.
- NEVER scale or adjust quantities/servings — faithful extraction only.
- Always set source_url to the exact URL fetched.
- `ingredients`/`steps` always carry the WHOLE page, even when `components` is
  filled — components is extra reporting on top, never a replacement.
"""


def _sanitize_extracted_servings(recipe: Recipe | None) -> Recipe | None:
    """Map implausible extracted serving counts to 0 ("page stated none").

    Attached as an output validator to BOTH extraction agents, which is the one
    place every extracted `Recipe` passes through — `chat.py` reads `.output` at
    five separate call sites, and a check repeated five times is a check that
    eventually gets forgotten at a sixth.

    The case this exists for: a page whose portions field carries a yield weight
    ("Liczba porcji: 2000g"). The extractor is doing exactly as instructed —
    copying the count stated on the page — so the correction belongs here, after
    faithful extraction, not as an exception carved into the verbatim rule (Rule 5).

    A validator that RETURNS a corrected value rather than raising `ModelRetry` on
    purpose: the extraction is good, only this one field is not a portion count,
    and re-running the model would spend tokens re-reading the same label.
    """
    if recipe is None:
        return None
    clean = sanitize_servings(recipe.servings)
    blocks = [
        b.model_copy(update={"servings": sanitize_servings(b.servings)})
        if sanitize_servings(b.servings) != b.servings
        else b
        for b in recipe.components
    ]
    if clean == recipe.servings and blocks == recipe.components:
        return recipe
    log.warning(
        "extracted_servings_implausible",
        source_url=recipe.source_url,
        recipe_name=recipe.name,
        reported_servings=recipe.servings,
    )
    return recipe.model_copy(update={"servings": clean, "components": blocks})


def build_web_search_agent(config: TenantConfig) -> Agent[None, Recipe | None]:
    """Agent that searches DDG then fetches and extracts a recipe from the best result."""
    agent = Agent(
        config.model_web_search,
        output_type=Recipe | None,
        defer_model_check=True,
        tools=[
            duckduckgo_search_tool(max_results=_WEB_SEARCH_MAX_RESULTS),
            recipe_web_fetch_tool(_MAX_PAGE_CONTENT),
        ],
        instructions=(
            f"You are {config.persona}.\n"
            f"You MUST respond exclusively in {config.language}. "
            f"Every field — name, description, ingredients, steps, tips — must be in {config.language}.\n"
            + _SEARCH_INSTRUCTIONS
        ),
    )
    agent.output_validator(_sanitize_extracted_servings)
    return agent


def build_web_fetch_agent(
    config: TenantConfig, pinned_url: str | None = None
) -> Agent[None, Recipe | None]:
    """Agent that fetches a known URL and extracts the recipe — no search needed.

    Pass `pinned_url` when the exact page is already known (a pasted link, or a
    proposal's source_url): the fetch tool then ignores the model's retyped URL
    argument, which is a real corruption source on long slugs. Agents built with
    a pinned URL are per-URL and must NOT be cached across different URLs.
    """
    agent = Agent(
        config.model_web_search,
        output_type=Recipe | None,
        defer_model_check=True,
        tools=[recipe_web_fetch_tool(_MAX_PAGE_CONTENT, pinned_url=pinned_url)],
        instructions=(
            f"You are {config.persona}.\n"
            f"You MUST respond exclusively in {config.language}. "
            f"Every field — name, description, ingredients, steps, tips — must be in {config.language}.\n"
            + _EXTRACT_INSTRUCTIONS
        ),
    )
    agent.output_validator(_sanitize_extracted_servings)
    return agent


def web_search_prompt(ingredients: ParsedIngredients, intent: UserIntent, site_filter: str = "") -> str:
    dish = intent.dish_type if intent.dish_type != "any" else "przepis"
    ingr = f" {', '.join(ingredients.items)}" if ingredients.items else ""
    base_query = f"{dish}{ingr}"

    if site_filter:
        # Unparenthesised — `(site:a OR site:b)` is unreliable on DDG.
        search_query = f"{base_query} {site_filter}"
    else:
        search_query = base_query

    parts = [f'Search query to use verbatim: "{search_query}"']
    if ingredients.dietary_hints:
        parts.append(f"Dietary requirements: {', '.join(ingredients.dietary_hints)}")
    if intent.max_time_minutes:
        parts.append(f"Time budget: under {intent.max_time_minutes} minutes")
    if intent.servings:
        parts.append(f"Servings: {intent.servings}")
    if intent.free_notes:
        parts.append(f"Notes: {intent.free_notes}")
    return "\n".join(parts)


def web_fetch_prompt_split_retry(url: str, servings: list[int]) -> str:
    """Re-extract a page we KNOW has several serving-count headings.

    Used only when a deterministic scan of the fetched text found headings the
    model's `components` didn't report (see `models/recipe_blocks.py`). Measured
    live: gpt-4o-mini leaves `components=[]` on the curry+naan page a fair share
    of the time even with the counting instruction, and an empty list looks
    exactly like a genuine single-recipe page — so the caller tells the model what
    the page demonstrably contains rather than asking the same question twice.

    The counts are stated as FACTS about the page, not as content to invent: the
    model still copies every ingredient verbatim from the markdown (Rule 5).
    """
    counts = ", ".join(str(s) for s in servings)
    return (
        f"Fetch and extract the recipe from this URL, exactly as written: {url}\n\n"
        f"IMPORTANT — this page contains SEVERAL ingredient lists. A scan of the "
        f"page text found these serving-count headings, in order: {counts}. "
        f"You MUST return one `components` entry per heading, in page order, the "
        f"main recipe first, each with that heading's own serving count and its "
        f"own ingredients/steps copied verbatim from the page. Do not return an "
        f"empty `components` list, and do not merge the lists together."
    )


def web_fetch_prompt(url: str) -> str:
    # Extraction is verbatim: we do NOT tell the model a target serving count here.
    # Passing "adjust servings to N" made the model rescale quantities during
    # extraction (inflating amounts that were already correct) and drop ingredients
    # while doing the arithmetic. Scaling to the user's serving count, when needed,
    # is a separate concern handled after a faithful extraction.
    return f"Fetch and extract the recipe from this URL, exactly as written: {url}"
