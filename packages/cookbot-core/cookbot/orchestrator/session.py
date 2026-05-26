import asyncio
from collections.abc import Awaitable, Callable

import structlog

from cookbot.agents.recipe_gen import build_recipe_gen_agent, recipe_gen_prompt
from cookbot.agents.refinement import build_refinement_agent, refinement_prompt
from cookbot.agents.web_search import build_web_search_agent, web_search_prompt
from cookbot.hitl.gate import HITLGate
from cookbot.hitl.models import HITLResponse
from cookbot.models.recipe import ParsedIngredients, Recipe, RecipeSource, UserIntent
from cookbot.models.session import Message
from cookbot.models.tenant import TenantConfig
from cookbot.services.firestore import FirestoreService

log = structlog.get_logger()


class SessionOrchestrator:
    def __init__(self, config: TenantConfig, firestore: FirestoreService) -> None:
        self._config = config
        self._firestore = firestore

    async def run(
        self,
        session_id: str,
        intent: UserIntent,
        ingredients: ParsedIngredients,
        gate: HITLGate,
        on_token: Callable[[str], Awaitable[None]],
        on_agent_update: Callable[[str, str], Awaitable[None]],
        on_final_recipe: Callable[[Recipe, RecipeSource], Awaitable[None]],
        on_error: Callable[[str], Awaitable[None]],
    ) -> None:
        try:
            # ── Step 1: WebSearchAgent ────────────────────────────────────
            await on_agent_update("web_search", "searching")
            ws_agent = build_web_search_agent(self._config)
            ws_result = await ws_agent.run(web_search_prompt(ingredients, intent))
            recipe: Recipe | None = ws_result.output
            source = RecipeSource.WEB_SEARCH

            # ── Step 2: RecipeGenAgent fallback ───────────────────────────
            if recipe is None:
                await on_agent_update("recipe_gen", "generating")
                gen_agent = build_recipe_gen_agent(self._config)
                gen_result = await gen_agent.run(recipe_gen_prompt(ingredients, intent))
                recipe = gen_result.output
                source = RecipeSource.AI_GENERATED

            await self._save_message(session_id, f"Recipe generated: {recipe.name}")

            # ── Step 3: HITL loop ─────────────────────────────────────────
            # gate.suspend() saves the checkpoint, signals the WS handler via
            # checkpoint_q, then blocks until the WS handler calls submit_response().
            refinement_agent = build_refinement_agent(self._config)
            for round_num in range(1, self._config.max_hitl_rounds + 1):
                response: HITLResponse = await gate.suspend(recipe, round_number=round_num)

                if response.approved:
                    break

                if response.modification is None:
                    await on_error("Recipe rejected. Please start a new search.")
                    return

                await on_agent_update("refinement", f"refining round {round_num}")
                ref_result = await refinement_agent.run(
                    refinement_prompt(recipe, response.modification)
                )
                recipe = ref_result.output
                source = RecipeSource.AI_GENERATED
                await self._save_message(session_id, f"Recipe refined: {recipe.name}")
            else:
                await on_token(self._config.ui.max_rounds_reached)

            await on_final_recipe(recipe, source)
            await self._save_message(session_id, f"Final recipe delivered: {recipe.name}")

        except asyncio.CancelledError:
            log.info("orchestrator_cancelled", session_id=session_id)
            raise
        except Exception as exc:
            log.exception("orchestrator_error", session_id=session_id, error=str(exc))
            await on_error(f"Something went wrong: {exc}")

    async def _save_message(self, session_id: str, content: str) -> None:
        await self._firestore.save_message(session_id, Message(role="assistant", content=content))
