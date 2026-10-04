"""What kind of task a user message is, decided by OpenJev."""

from __future__ import annotations

from code_ai.core.planning.models import TaskDecision, TaskSignals, is_continuation_request
from code_ai.decisions.openjev import OpenJevClient, choice, chosen

# One question, on purpose. On a llama.cpp server a request costs about the
# same whatever its length, so everything the planner needs is read off a
# single choice instead of a yes/no per signal: three passes became one.
#
# The questions are in English and the message in whatever the user wrote; the
# engine reads both.
_CHANGES = {"bug_fix", "refactoring", "implementation"}
# The intents that cannot be served without the project's files.
_LOCAL = _CHANGES | {"run_command", "inspect_project"}
_QUESTIONS = {
    "intent": choice(
        "A user sent this message to a coding assistant that works inside their "
        "project. What is the user mainly asking the assistant to do?",
        {
            "bug_fix": "Fix something in the code that is broken or behaves wrongly",
            "refactoring": "Restructure or clean up existing code without changing what it does",
            "implementation": "Create, add, edit, rename or delete files or code",
            "run_command": "Run a command, a build, a script or the tests, without changing code",
            "inspect_project": "Read, find, list, explain or review something in the project",
            "web_research": "Look up current information on the internet",
            "continue": "Carry on with the work already in progress, stating nothing new to do",
            "conversation": "Chat, greet, or ask a general question not about this project",
        },
    )
}


class OpenJevTaskClassifier:
    """Reads the message with the engine, and with the keywords when it is unsure.

    The engine's reading counts only when its probability clears ``confidence``;
    below that the keyword reading stands whole. A wrong confident guess is
    what costs a turn, so an unsure engine is treated as no engine.
    """

    def __init__(self, client: OpenJevClient, *, confidence: float = 0.65) -> None:
        self._client = client
        self._confidence = confidence

    async def __call__(self, text: str) -> TaskDecision | None:
        if not self._client.available:
            return None
        answers = await self._client.system_one(text, _QUESTIONS)
        intent, probability = chosen(answers, "intent")
        if probability < self._confidence:
            return TaskDecision(
                signals=TaskSignals.from_keywords(text),
                continuation=is_continuation_request(text),
            )
        web = intent == "web_research"
        return TaskDecision(
            signals=TaskSignals(
                mutation=intent in _CHANGES,
                command=intent == "run_command",
                local_reference=intent in _LOCAL,
                explicit_web=web,
                external=web,
                current_info=web,
                bug_fix=intent == "bug_fix",
                refactoring=intent == "refactoring",
            ),
            # The closed vocabulary is never wrong when it fires; the engine adds
            # the phrasings it does not know.
            continuation=intent == "continue" or is_continuation_request(text),
        )
