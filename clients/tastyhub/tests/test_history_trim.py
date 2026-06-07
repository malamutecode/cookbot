"""Regression tests for WS history trimming.

Bug: the old trim cut at the first message with kind=="request", but a
tool-return is ALSO a ModelRequest. Cutting there left a tool message without
its preceding tool_calls response, producing an OpenAI 400:
  "messages with role 'tool' must be a response to a preceeding message with
   'tool_calls'."
The fix only cuts at a ModelRequest that contains a real UserPromptPart.
"""
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from app.api.websocket import _is_user_turn, _safe_history_cut


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _tool_call(name: str, call_id: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name=name, args={}, tool_call_id=call_id)])


def _tool_return(name: str, call_id: str) -> ModelRequest:
    return ModelRequest(parts=[ToolReturnPart(tool_name=name, content="ok", tool_call_id=call_id)])


def _text(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def test_is_user_turn_distinguishes_prompt_from_tool_return() -> None:
    assert _is_user_turn(_user("hi")) is True
    assert _is_user_turn(_tool_return("update_onboarding", "1")) is False
    assert _is_user_turn(_text("hello")) is False


def test_safe_cut_never_lands_on_a_tool_return() -> None:
    # Pattern from an onboarding turn: user → tool_call → tool_return → text,
    # repeated. A naive "first kind==request" cut would land on a tool_return.
    history: list = []
    for i in range(6):
        history.append(_user(f"answer {i}"))
        history.append(_tool_call("update_onboarding", str(i)))
        history.append(_tool_return("update_onboarding", str(i)))
        history.append(_text(f"next question {i}"))

    cut = _safe_history_cut(history, keep_at_least=10)

    # The chosen cut index must be a real user turn (never a tool-return).
    assert _is_user_turn(history[cut])
    # And the slice must not start with an orphaned tool-return.
    sliced = history[cut:]
    assert _is_user_turn(sliced[0])


def test_safe_cut_returns_zero_when_no_user_turn_in_window() -> None:
    # If the tail (last keep_at_least) has no user turn, don't trim at all.
    history: list = [_user("start")]
    for i in range(12):
        history.append(_tool_call("t", str(i)))
        history.append(_tool_return("t", str(i)))
    cut = _safe_history_cut(history, keep_at_least=10)
    assert cut == 0  # no safe point in window → keep everything
