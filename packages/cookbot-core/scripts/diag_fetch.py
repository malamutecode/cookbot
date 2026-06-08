"""Diagnostic: what do real PL recipe pages actually return, and do they carry
JSON-LD Recipe schema? Run manually:

    uv run python scripts/diag_fetch.py

Not a test — a throwaway probe to decide STEP 39's extraction approach.
"""
from __future__ import annotations

import asyncio
import json
import re

import httpx

URLS = [
    "https://www.ofeminin.pl/kuchnia/przepisy/szybki-obiad-pyszny-makaron-ze-szpinakiem-serem-feta-i-sosem-pomidorowym/rbynp27",
    "https://aniagotuje.pl/przepis/makaron-ze-szpinakiem",
    "https://www.kwestiasmaku.com/przepis/makaron-ze-szpinakiem",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def find_jsonld_recipes(html: str) -> list[dict]:
    """Extract any JSON-LD blocks that describe a Recipe (handles @graph + arrays)."""
    blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE,
    )
    recipes: list[dict] = []
    for b in blocks:
        try:
            data = json.loads(b.strip())
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        for c in candidates:
            if not isinstance(c, dict):
                continue
            graph = c.get("@graph", [c])
            for node in (graph if isinstance(graph, list) else [graph]):
                if not isinstance(node, dict):
                    continue
                t = node.get("@type", "")
                types = t if isinstance(t, list) else [t]
                if "Recipe" in types:
                    recipes.append(node)
    return recipes


async def probe(url: str) -> None:
    print("=" * 80)
    print("URL:", url)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
            r = await client.get(url, headers=_HEADERS)
        html = r.text
        print("status:", r.status_code, "| bytes:", len(html))
        # Raw diagnostics
        all_ldjson = re.findall(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.DOTALL | re.IGNORECASE,
        )
        print("total ld+json <script> blocks:", len(all_ldjson))
        for i, b in enumerate(all_ldjson[:6]):
            snippet = b.strip().replace("\n", " ")[:140]
            print(f"  [{i}] {snippet}")
        print('mentions "Recipe":', '"Recipe"' in html,
              '| recipeIngredient:', "recipeIngredient" in html,
              '| itemtype Recipe:', "schema.org/Recipe" in html)
        recipes = find_jsonld_recipes(html)
        print("JSON-LD Recipe blocks found:", len(recipes))
        # Microdata probe
        ingr_props = re.findall(r'itemprop=["\']recipeIngredient["\'][^>]*>([^<]{0,80})', html)
        name_props = re.findall(r'itemprop=["\']name["\'][^>]*>([^<]{0,80})', html)
        instr_props = re.findall(r'itemprop=["\'](?:recipeInstructions|text)["\']', html)
        print("microdata recipeIngredient matches:", len(ingr_props), "→", [i.strip() for i in ingr_props[:3]])
        print("microdata name matches:", len(name_props), "→", [n.strip() for n in name_props[:2]])
        print("microdata instruction props:", len(instr_props))
        if recipes:
            rec = recipes[0]
            ingredients = rec.get("recipeIngredient", [])
            instructions = rec.get("recipeInstructions", [])
            print("  name:", rec.get("name"))
            print("  ingredients:", len(ingredients), "→", ingredients[:3])
            print("  instructions type:", type(instructions).__name__, "count:",
                  len(instructions) if isinstance(instructions, list) else "n/a")
            img = rec.get("image")
            print("  image:", img if isinstance(img, str) else (img.get("url") if isinstance(img, dict) else img))
    except Exception as exc:
        print("FETCH ERROR:", type(exc).__name__, exc)


async def main() -> None:
    for u in URLS:
        await probe(u)


if __name__ == "__main__":
    asyncio.run(main())
