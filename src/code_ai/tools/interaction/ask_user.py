from __future__ import annotations

from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.core.interaction import Question
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.tools.schema import tool_schema


class AskUserTool:
    name = "ask_user"
    description = (
        "Ask the user one blocking question that cannot be answered safely by "
        "inspecting the project. Offer the answers you are choosing between in "
        "'options': the user picks one on a card instead of writing prose, which "
        "is the difference between a spec getting clarified and a question being "
        "ignored. When a vague request leaves you with several independent "
        "unknowns, call this once per unknown in the same step - they are shown "
        "together as one numbered questionnaire, one question per page. Calling "
        "this ends the current turn; the user's reply is their answer."
    )
    capabilities = frozenset({ToolCapability.INTERACTION})
    input_schema = tool_schema(
        {
            "question": {
                "type": "string",
                "description": "The single blocking question to put to the user.",
            },
            "header": {
                "type": "string",
                "description": (
                    "Two or three words naming what this question is about (e.g. "
                    "'Database', 'Auth method'). Labels the card and the answer."
                ),
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "The answers to offer, each becoming a card the user can press. "
                    "Write 'Label :: why it might be the right call' to explain an "
                    "option; a bare label is fine when it needs no explanation. "
                    "Leave empty only when the answer cannot be anticipated."
                ),
            },
            "multi_select": {
                "type": "boolean",
                "description": (
                    "Whether several options can be chosen at once. Use it when the "
                    "options are not mutually exclusive."
                ),
            },
            "allow_other": {
                "type": "boolean",
                "description": (
                    "Whether the user may write an answer of their own instead of "
                    "picking one. Defaults to true; set false only when the options "
                    "are genuinely exhaustive."
                ),
            },
            "why_required": {
                "type": "string",
                "description": (
                    "One sentence on what is blocked until this is answered. Shown "
                    "to the user as the reason they are being interrupted."
                ),
            },
        },
        required=("question",),
    )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        prompt = str(arguments.get("question") or "").strip()
        if not prompt:
            raise ToolArgumentError("question is required.")
        _reject_non_strings(arguments.get("options"))
        question = Question.from_payload(
            {
                **arguments,
                "question": prompt,
                "why_required": (
                    str(arguments.get("why_required") or "").strip()
                    or "The task is blocked on a user decision."
                ),
            }
        )
        assert question is not None  # a non-empty prompt always parses
        payload = question.to_payload()
        await context.event_bus.emit(
            "interaction.question.requested",
            payload,
            source="tool.ask_user",
        )
        # The orchestrator ends the turn when it sees this result, so the model
        # never reads this message mid-turn; it reads it on the *next* turn as
        # history, where it must explain that the question already went out.
        return {
            "status": "blocked",
            **payload,
            "message": (
                "Question delivered to the user; this turn ends now. "
                "The user's next message is their reply."
            ),
        }


def _reject_non_strings(value: object) -> None:
    """Fail loudly on a malformed options list instead of silently dropping it.

    Parsing tolerates blanks and missing descriptions, which are a model being
    sloppy. A list of numbers or objects is a model misreading the schema, and
    quietly showing a question with no cards would hide that.
    """

    if value is None:
        return
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ToolArgumentError("options must be a list of strings.")
