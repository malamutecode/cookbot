from pydantic_ai import Agent

from cookbot.models.recipe import UserIntent
from cookbot.models.tenant import TenantConfig

def build_intake_agent(config: TenantConfig) -> Agent[None, UserIntent]:
    return Agent(
        config.model,
        output_type=UserIntent,
        defer_model_check=True,
        instructions=f"""
You are {config.persona}.
You MUST respond exclusively in {config.language}. All output values must be written in {config.language}. Never use any other language.

The user answered five onboarding questions. Extract a structured UserIntent from their answers.

Rules:
- dish_type: capture the user's dish preference as-is (free text). If they said "surprise me",
  "suggest based on my ingredients", or left it open, use "any".
- servings: integer number of portions. "just me" or "1 person" → 1. "for two" → 2.
  If not specified or unclear → 0.
- max_time_minutes: convert their time answer to an integer number of minutes.
  "no rush" or "doesn't matter" → 0. "half an hour" → 30. "1 hour" → 60.
- available_ingredients: list of ingredients they mentioned. Empty list if they said "no" or nothing.
- free_notes: any extra context, preferences, or constraints the user mentioned (e.g. "easy to
  reheat", "no spicy food", "kid-friendly"). Use the user's own words, kept concise. Empty string
  if they said "no" or provided nothing relevant.

Always return valid JSON matching the UserIntent schema. Do not add commentary.
""",
    )
