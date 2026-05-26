from enum import Enum

from pydantic import BaseModel


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


class RecipeSource(str, Enum):
    TENANT_KB = "TENANT_KB"
    WEB_SEARCH = "WEB_SEARCH"
    AI_GENERATED = "AI_GENERATED"


class RecipeSearchResult(BaseModel):
    recipe: Recipe
    source: RecipeSource
    similarity_score: float
