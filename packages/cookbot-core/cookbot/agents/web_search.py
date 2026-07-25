import re

import httpx
from markdownify import markdownify as md
from pydantic_ai import Agent
from pydantic_ai._ssrf import safe_download
from pydantic_ai.common_tools.duckduckgo import duckduckgo_search_tool
from pydantic_ai.common_tools.web_fetch import WebFetchLocalTool, WebFetchResult
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import BinaryContent
from pydantic_ai.tools import Tool

from cookbot.models.recipe import ParsedIngredients, Recipe, UserIntent
from cookbot.models.tenant import TenantConfig

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
    stock = WebFetchLocalTool(
        max_content_length=max_content_length,
        allow_local_urls=False,
        timeout=_FETCH_TIMEOUT_SECONDS,
    )

    async def web_fetch(url: str) -> WebFetchResult | BinaryContent:
        """Fetch the content of a web page at the given URL and return it as markdown."""
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
            return await stock(url)

        html = response.text
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else ""

        content = md(_strip_html_noise(html), strip=["img", "script", "style"])
        content = _EXCESS_NEWLINES_RE.sub("\n\n", content).strip()
        if len(content) > max_content_length:
            content = content[:max_content_length]

        return WebFetchResult(url=str(response.url), title=title, content=content)

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
     (e.g. "sos", "do podania"). Common items are easy to overlook: onion (cebula),
     garlic (czosnek), salt, pepper, oil/butter — double-check none are missing.
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
   - servings: the serving count STATED ON THE PAGE (e.g. "porcje: 2" → 2). Do not
     guess or change it. If the page gives none, use 0.
   - tips: practical tips from the page (empty list if none)
   - source_url: the URL you fetched (copy exactly)
   - image_url: og:image URL if visible in the markdown; otherwise null
3. Only return null if the page genuinely has NO recipe (a 404 page, a paywall,
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
   - servings: the serving count stated on the page (0 if none given); do not change it
   - tips: practical tips from the page (empty list if none)
   - source_url: the URL you fetched (copy exactly)
   - image_url: og:image URL if visible in the markdown; otherwise null
5. If the page has no real recipe, try the second-best URL from step 1.
   If none work, return null.

## Rules
- NEVER invent ingredients or steps — extract only what is on the page.
- NEVER scale or adjust quantities/servings — faithful extraction only.
- Always set source_url to the exact URL fetched.
"""


def build_web_search_agent(config: TenantConfig) -> Agent[None, Recipe | None]:
    """Agent that searches DDG then fetches and extracts a recipe from the best result."""
    return Agent(
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


def build_web_fetch_agent(
    config: TenantConfig, pinned_url: str | None = None
) -> Agent[None, Recipe | None]:
    """Agent that fetches a known URL and extracts the recipe — no search needed.

    Pass `pinned_url` when the exact page is already known (a pasted link, or a
    proposal's source_url): the fetch tool then ignores the model's retyped URL
    argument, which is a real corruption source on long slugs. Agents built with
    a pinned URL are per-URL and must NOT be cached across different URLs.
    """
    return Agent(
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


def web_fetch_prompt(url: str) -> str:
    # Extraction is verbatim: we do NOT tell the model a target serving count here.
    # Passing "adjust servings to N" made the model rescale quantities during
    # extraction (inflating amounts that were already correct) and drop ingredients
    # while doing the arithmetic. Scaling to the user's serving count, when needed,
    # is a separate concern handled after a faithful extraction.
    return f"Fetch and extract the recipe from this URL, exactly as written: {url}"
