"""Questions the agent puts to the user, and the answers that come back.

``ask_user`` used to carry a single string, which meant a badly specified task
turned into a wall of prose the user had to answer by writing an essay. The
shape here is the one that actually fits the problem: several small questions,
each with the options the agent already has in mind, so answering an ambiguous
spec is a matter of choosing rather than composing.

The model owns no rendering and no UI. It exists so three very different
consumers agree on the same thing:

- the terminal, which turns each question into a page of clickable cards;
- the transcript and any headless client, which get a numbered text form and a
  user who answers by typing a number;
- the model, which reads the answers back as one plain, unambiguous message.

Keeping that in one place is what stops the three from drifting into three
different notions of what question 2 was.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

# Separates an option's label from its explanation inside a single string.
# Options travel as a flat list of strings rather than a list of objects
# because the tool schemas here stay atomic for the sake of small local models,
# which handle "Postgres :: strong consistency" far better than nested JSON.
OPTION_SEPARATOR = "::"

# A card gets a number key, and there are only so many of those worth binding.
MAX_KEYED_OPTIONS = 9


@dataclass(frozen=True, slots=True)
class QuestionOption:
    """One answer the agent is offering, with the reason it is on the list."""

    label: str
    description: str = ""

    @classmethod
    def parse(cls, raw: str) -> QuestionOption | None:
        """Read ``Label :: why it might be the right call`` into an option.

        An option with no separator is just a label; an empty one is dropped, so
        a model that pads its list with blanks does not produce empty cards.
        """

        text = str(raw or "").strip()
        if not text:
            return None
        label, separator, description = text.partition(OPTION_SEPARATOR)
        if not separator:
            return cls(label=text)
        label = label.strip()
        if not label:
            return None
        return cls(label=label, description=description.strip())

    def render(self) -> str:
        """How the option reads to a person."""

        return f"{self.label} - {self.description}" if self.description else self.label

    def serialize(self) -> str:
        """How the option travels on the wire, so parsing it back is lossless."""

        if not self.description:
            return self.label
        return f"{self.label} {OPTION_SEPARATOR} {self.description}"


@dataclass(frozen=True, slots=True)
class Question:
    """One thing the agent needs decided before it can carry on."""

    prompt: str
    header: str = ""
    why_required: str = ""
    options: tuple[QuestionOption, ...] = ()
    multi_select: bool = False
    allow_other: bool = True

    @property
    def title(self) -> str:
        """Short label for the card's tab. Falls back to a clipped prompt."""

        if self.header:
            return self.header
        clipped = self.prompt.strip().splitlines()[0] if self.prompt.strip() else ""
        return clipped[:32].rstrip(" .,:;-") or "Pergunta"

    @property
    def keyed_options(self) -> tuple[QuestionOption, ...]:
        """The options that get a number key, which is all a keyboard can offer."""

        return self.options[:MAX_KEYED_OPTIONS]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Question | None:
        prompt = str(payload.get("question") or "").strip()
        if not prompt:
            return None
        raw_options = payload.get("options")
        if not isinstance(raw_options, list):
            raw_options = []
        parsed = [QuestionOption.parse(item) for item in raw_options if isinstance(item, str)]
        return cls(
            prompt=prompt,
            header=str(payload.get("header") or "").strip(),
            why_required=str(payload.get("why_required") or "").strip(),
            options=tuple(option for option in parsed if option is not None),
            multi_select=bool(payload.get("multi_select", False)),
            # A question with no options is free-form whatever the flag says:
            # refusing free text there would leave nothing to answer with.
            allow_other=bool(payload.get("allow_other", True)) or not parsed,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "question": self.prompt,
            "header": self.header,
            "why_required": self.why_required,
            "options": [option.serialize() for option in self.options],
            "multi_select": self.multi_select,
            "allow_other": self.allow_other,
        }


@dataclass(frozen=True, slots=True)
class Questionnaire:
    """Every question one turn ended on, in the order the agent asked them."""

    questions: tuple[Question, ...] = ()

    def __len__(self) -> int:
        return len(self.questions)

    def __iter__(self):
        return iter(self.questions)

    def __getitem__(self, index: int) -> Question:
        return self.questions[index]

    @property
    def is_empty(self) -> bool:
        return not self.questions

    @classmethod
    def from_payloads(cls, payloads: Sequence[Mapping[str, Any]]) -> Questionnaire:
        parsed = [Question.from_payload(payload) for payload in payloads]
        return cls(questions=tuple(item for item in parsed if item is not None))

    def render_text(self) -> str:
        """The questionnaire as text, for the transcript and for clients with no UI.

        Numbered throughout - questions and their options - so a user reading it
        in a plain terminal can answer "2" or "1: postgres, 2: sim" and be
        understood. This is the only form some clients will ever see, so it has
        to stand on its own.
        """

        if self.is_empty:
            return ""
        total = len(self.questions)
        blocks: list[str] = []
        for index, question in enumerate(self.questions, start=1):
            blocks.append(_render_question(question, index=index, total=total))
        body = "\n\n".join(blocks)
        if total == 1:
            return body
        return f"Preciso de {total} respostas para seguir:\n\n{body}"


@dataclass(frozen=True, slots=True)
class Answer:
    """What the user decided for one question."""

    question: Question
    chosen: tuple[str, ...] = field(default_factory=tuple)
    other: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.chosen and not self.other.strip()

    def render(self) -> str:
        """The answer as the model should read it: the choice, then any addition."""

        parts = list(self.chosen)
        extra = self.other.strip()
        if extra:
            parts.append(extra if not parts else f"além disso: {extra}")
        return ", ".join(parts) if parts else "(sem resposta)"


def render_answers(answers: Sequence[Answer]) -> str:
    """Fold the answered cards into the single message the model reads.

    Each line repeats the question before its answer. The model asked these in
    one batch and reads them one turn later, where "Postgres" on its own would
    be ambiguous between three questions.
    """

    lines: list[str] = []
    for index, answer in enumerate(answers, start=1):
        if answer.is_empty:
            continue
        lines.append(f"{index}. {answer.question.title}: {answer.render()}")
    return "\n".join(lines)


def _render_question(question: Question, *, index: int, total: int) -> str:
    lines: list[str] = []
    if total > 1:
        heading = f"Pergunta {index} de {total}"
        if question.header:
            heading += f" · {question.header}"
        lines.append(heading)
    lines.append(question.prompt)
    for position, option in enumerate(question.options, start=1):
        lines.append(f"  [{position}] {option.render()}")
    if question.options:
        hint = (
            "  (responda pelos números, separados por vírgula)"
            if question.multi_select
            else "  (responda pelo número)"
        )
        if question.allow_other:
            hint = hint[:-1] + ", ou escreva a sua resposta)"
        lines.append(hint)
    return "\n".join(lines)
