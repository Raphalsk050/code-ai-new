"""Whether the answer a turn stopped on finishes the request, decided by OpenJev."""

from __future__ import annotations

from code_ai.core.planning.models import FinalAnswerQuery, FinalAnswerVerdict
from code_ai.decisions.openjev import OpenJevClient, choice, chosen
from code_ai.tools.output import bound_text

# The request and the tail of the reply are what the verdict hangs on, and a
# short state is a fast answer: this runs at the end of every working turn.
_OBJECTIVE_CHARS = 1500
_ACTIONS_CHARS = 1500
_ANSWER_CHARS = 2500

_INSTRUCTIONS = (
    "A coding assistant was given the user request above, took the recorded "
    "actions, and then stopped with the final reply. Which option describes "
    "where that leaves the request?"
)
_OPTIONS = {
    FinalAnswerVerdict.DONE: (
        "Everything the user asked for was delivered: the work is done, or the "
        "question is answered"
    ),
    FinalAnswerVerdict.ANNOUNCED: (
        "The reply says what the assistant is going to do next ('I will now...', "
        "'vou criar...') but stops before doing it"
    ),
    FinalAnswerVerdict.UNAPPLIED: (
        "The user asked for files to be created or changed, and the reply only "
        "shows the code or the steps as text instead of changing the files"
    ),
    FinalAnswerVerdict.INCOMPLETE: (
        "Part of what the user asked for was done and part of it was left out"
    ),
    FinalAnswerVerdict.ASKS_USER: (
        "The reply asks the user a question, or for a choice or a confirmation, "
        "that it needs before going on"
    ),
}
# The verdicts that send the model back to work.
_UNFINISHED = {
    FinalAnswerVerdict.ANNOUNCED,
    FinalAnswerVerdict.UNAPPLIED,
    FinalAnswerVerdict.INCOMPLETE,
}


class OpenJevFinalAnswerJudge:
    """One choice, one pass. Sends the model back only when the engine is sure.

    A nudge costs a model round trip, so an unfinished verdict takes
    ``nudge_confidence``; anything less lets the answer stand.
    """

    def __init__(self, client: OpenJevClient, *, nudge_confidence: float = 0.8) -> None:
        self._client = client
        self._nudge_confidence = nudge_confidence

    async def __call__(self, query: FinalAnswerQuery) -> FinalAnswerVerdict | None:
        if not self._client.available:
            return None
        options = {
            verdict.value: description
            for verdict, description in _OPTIONS.items()
            # With files changed the change was applied, whatever else is true.
            if not (query.changed_files and verdict is FinalAnswerVerdict.UNAPPLIED)
        }
        answers = await self._client.system_one(
            _render(query), {"verdict": choice(_INSTRUCTIONS, options)}
        )
        value, _ = chosen(answers, "verdict")
        verdict = FinalAnswerVerdict(value)
        if verdict not in _UNFINISHED:
            return verdict
        # "Is it unfinished" is what a nudge hangs on, not which kind: a reply
        # split between announced and incomplete is unfinished either way, so
        # the three share one threshold and the likeliest names the correction.
        probabilities = answers["verdict"]["probabilities"]
        unfinished = sum(
            float(probabilities.get(option.value, 0.0)) for option in _UNFINISHED
        )
        return verdict if unfinished >= self._nudge_confidence else FinalAnswerVerdict.DONE


def _render(query: FinalAnswerQuery) -> str:
    answer = query.answer.strip()
    if len(answer) > _ANSWER_CHARS:
        # The closing lines are where "next I will..." lives.
        answer = "…" + answer[-_ANSWER_CHARS:]
    return (
        "User request:\n"
        f"{bound_text(query.objective, _OBJECTIVE_CHARS)}\n\n"
        "Actions the assistant took (tool calls, in order):\n"
        f"{bound_text(query.actions, _ACTIONS_CHARS) or '(none - no tool was used)'}\n\n"
        f"Files changed: {'yes' if query.changed_files else 'no'}\n\n"
        "Assistant's final reply:\n"
        f"{answer}"
    )
