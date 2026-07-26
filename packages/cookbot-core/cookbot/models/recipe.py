from enum import StrEnum

from pydantic import BaseModel

from cookbot.models.recipe_blocks import RecipeBlock


class UserIntent(BaseModel):
    dish_type: str                   # free text: "pasta", "soup", "surprise me", etc.
    max_time_minutes: int            # 0 = no constraint
    servings: int                    # number of portions; 0 = not specified
    available_ingredients: list[str] # ingredients the user already has
    free_notes: str                  # any extra context from the user; "" if nothing


class ParsedIngredients(BaseModel):
    items: list[str]
    must_use: list[str]       # user wants these prioritised (expiring, seasonal, etc.)
    dietary_hints: list[str]
    missing_staples: list[str]


class Recipe(BaseModel):
    name: str
    description: str
    ingredients: list[str]
    steps: list[str]
    prep_time_minutes: int
    cook_time_minutes: int
    difficulty: str  # "Easy" | "Medium" | "Hard"
    servings: int
    tips: list[str]
    source_url: str | None = None   # set when recipe found via web search
    image_url: str | None = None    # og:image or prominent photo from the recipe page
    original_servings: int | None = None  # serving count as written on the source page,
    # before any scaling to the user's requested servings. None = never scaled / unknown.
    # Ingredient/step blocks the page carried, INCLUDING the main one at [0] (STEP 45).
    # Populated only when the page had more than one — a normal single-recipe page
    # leaves this empty, so the common path is untouched. The extractor merely
    # REPORTS what it saw (Rule 5); `models/recipe_blocks.py` decides whether a
    # block is a separate dish and the ChatAgent decides whether to ask.
    components: list[RecipeBlock] = []


class RecipeSummary(BaseModel):
    name: str
    description: str
    difficulty: str          # "Łatwe" | "Średnie" | "Trudne" (or English equiv)
    total_time_minutes: int
    key_ingredients: list[str]
    source: str              # "web_search" | "ai_generated"
    source_url: str | None = None  # page URL for web_search proposals — skip re-search on pick
    image_url: str | None = None  # dish photo thumbnail, set when available


class RecipeSource(StrEnum):
    TENANT_KB = "TENANT_KB"
    WEB_SEARCH = "WEB_SEARCH"
    AI_GENERATED = "AI_GENERATED"


class RecipeSearchResult(BaseModel):
    recipe: Recipe
    source: RecipeSource
    similarity_score: float
