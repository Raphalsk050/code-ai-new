"""The dialog that turns the agent's questions into cards the user presses.

A question the user has to answer by composing prose is a question that often
goes unanswered - so the questions a turn ended on are shown here as one page
each, with the agent's own candidate answers as cards. Pressing one is the
whole interaction; typing is the fallback, not the default.

One page per question, numbered and counted in the title, because a vague
request produces several unknowns at once and a single screen holding all of
them is exactly the wall of text this replaces.
"""

from __future__ import annotations

import asyncio

from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from code_ai.core.interaction import Answer, Question, Questionnaire, QuestionOption

_LABEL_STYLE = Style(color="#d7dee8", bold=True)
_KEY_STYLE = Style(color="#ff9f1c", bold=True)
_DESCRIPTION_STYLE = Style(color="#9fb3c8")
_MARK_STYLE = Style(color="#7ee787", bold=True)


class QuestionCard(Vertical):
    """One answer, pressable with the mouse, a number key, or Enter.

    Composed of child widgets rather than one multi-line renderable: a long
    description has to wrap, and only real children get their wrapped height
    measured - a hand-indented second line overflows the card's own border.
    """

    can_focus = True

    class Pressed(Message):
        """The user chose this card."""

        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    def __init__(self, option: QuestionOption, *, index: int, selected: bool) -> None:
        super().__init__(classes="question-card")
        self._option = option
        self._index = index
        self._selected = selected
        self.set_class(selected, "-selected")

    def compose(self) -> ComposeResult:
        yield Static(self._label(), classes="question-card-label", markup=False)
        if self._option.description:
            yield Static(
                self._option.description, classes="question-card-desc", markup=False
            )

    def _label(self) -> Text:
        # The mark column is always present, selected or not, so choosing an
        # option never shifts the label sideways.
        text = Text(no_wrap=False)
        text.append("✓ " if self._selected else "  ", style=_MARK_STYLE)
        text.append(f"[{self._index + 1}] ", style=_KEY_STYLE)
        text.append(self._option.label, style=_LABEL_STYLE)
        return text

    def set_selected(self, selected: bool) -> None:
        """Repaint just this card.

        Rebuilding the page instead would scroll the list back to the top and
        throw focus onto the first card, which on a multi-select question means
        losing your place after every single choice.
        """

        self._selected = selected
        self.set_class(selected, "-selected")
        self.query_one(".question-card-label", Static).update(self._label())

    def on_click(self) -> None:
        self.post_message(self.Pressed(self._index))

    def on_key(self, event: events.Key) -> None:
        if event.key in {"enter", "space"}:
            event.stop()
            self.post_message(self.Pressed(self._index))


class QuestionnaireModal(ModalScreen[list[Answer] | None]):
    """Every question of one turn, one page at a time.

    Dismisses with the answers, or with ``None`` when the user closes it - in
    which case the questions are still in the transcript and can be answered by
    typing, so escaping is never a dead end.
    """

    BINDINGS = [
        ("escape", "cancel", "Fechar"),
        ("left", "previous", "Anterior"),
        ("right", "advance", "Próxima"),
    ]

    def __init__(self, questionnaire: Questionnaire) -> None:
        super().__init__()
        self._questions = tuple(questionnaire)
        self._page = 0
        self._chosen: list[set[int]] = [set() for _ in self._questions]
        self._other: list[str] = ["" for _ in self._questions]
        self._cards: list[QuestionCard] = []

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        # markup=False throughout: the question text and its options come from
        # the model, and the key hints spell out "[Esc]" and "[Enter]" - both are
        # read as console markup otherwise, which silently eats the brackets and
        # everything between them.
        with Vertical(id="questions-dialog"):
            yield Static("", id="questions-title", markup=False)
            yield Static("", id="questions-why", markup=False)
            yield Static("", id="questions-prompt", markup=False)
            # The free-text field lives inside the scrolling area, below the
            # cards: it is one more way to answer, and keeping it out of the
            # fixed chrome is what leaves room for a card on a short terminal.
            with VerticalScroll(id="questions-cards"):
                yield Input(placeholder="Outra resposta…", id="questions-other")
            yield Static("", id="questions-keys", markup=False)
            with Horizontal(id="questions-actions"):
                yield Button("‹ Anterior", id="questions-previous")
                yield Button("Avançar ›", variant="primary", id="questions-advance")
                yield Button("Enviar", variant="success", id="questions-submit")

    async def on_mount(self) -> None:
        await self._render_page()

    @property
    def _question(self) -> Question:
        return self._questions[self._page]

    @property
    def _is_last(self) -> bool:
        return self._page >= len(self._questions) - 1

    async def _render_page(self) -> None:
        question = self._question
        total = len(self._questions)
        self.query_one("#questions-title", Static).update(self._title(total))
        why = self.query_one("#questions-why", Static)
        why.update(question.why_required)
        why.display = bool(question.why_required)
        self.query_one("#questions-prompt", Static).update(question.prompt)

        cards = self.query_one("#questions-cards", VerticalScroll)
        other = self.query_one("#questions-other", Input)
        for existing in self._cards:
            await existing.remove()
        chosen = self._chosen[self._page]
        self._cards = [
            QuestionCard(option, index=index, selected=index in chosen)
            for index, option in enumerate(question.keyed_options)
        ]
        # Mounted before the input so the cards read as the answer and the text
        # field as the way out of them.
        await cards.mount(*self._cards, before=other)

        other.value = self._other[self._page]
        other.display = question.allow_other
        other.placeholder = (
            "Sua resposta…" if not question.keyed_options else "Outra resposta…"
        )

        self._refresh_keys()
        self.query_one("#questions-previous", Button).disabled = self._page == 0
        self.query_one("#questions-advance", Button).display = not self._is_last
        self.query_one("#questions-submit", Button).display = self._is_last
        self._focus_first()

    def _title(self, total: int) -> Text:
        question = self._question
        text = Text(no_wrap=True)
        if total > 1:
            text.append(f"Pergunta {self._page + 1} de {total}", style=_KEY_STYLE)
            if question.header:
                text.append(f" · {question.header}", style=_LABEL_STYLE)
            text.append("   ")
            text.append(self._progress(), style=_DESCRIPTION_STYLE)
        else:
            text.append(question.header or "Pergunta do agente", style=_KEY_STYLE)
        return text

    def _progress(self) -> str:
        """Dots for the pages: current, already answered, still untouched."""

        marks: list[str] = []
        for index in range(len(self._questions)):
            if index == self._page:
                marks.append("◉")
            elif self._chosen[index] or self._other[index].strip():
                marks.append("●")
            else:
                marks.append("○")
        return " ".join(marks)

    def _refresh_keys(self) -> None:
        self.query_one("#questions-keys", Static).update(self._keys_hint())

    def _refresh_title(self) -> None:
        self.query_one("#questions-title", Static).update(self._title(len(self._questions)))

    def _keys_hint(self) -> str:
        """One line, short enough not to wrap in a narrow dialog."""

        parts: list[str] = []
        if self._question.keyed_options:
            parts.append("[1-9] escolher")
        if self._question.multi_select:
            parts.append("[Enter] confirmar")
        parts.append("[←/→] navegar")
        parts.append("[Esc] digitar")
        return " · ".join(parts)

    def _focus_first(self) -> None:
        """Put focus where the answer is, so the keyboard works without a click."""

        try:
            cards = self.query(QuestionCard)
            if cards:
                cards.first().focus()
                return
            self.query_one("#questions-other", Input).focus()
        except Exception:
            # A page with neither cards nor an input cannot happen, but focus is
            # never worth an exception in a dialog the user is trying to answer.
            pass

    # -- interaction -------------------------------------------------------

    def on_key(self, event: events.Key) -> None:
        # Only reached when the free-text field does not have focus, which is
        # what keeps "1" a shortcut here and a character there.
        if event.key.isdigit() and event.key != "0":
            index = int(event.key) - 1
            if index < len(self._question.keyed_options):
                event.stop()
                self._toggle(index)

    async def on_question_card_pressed(self, message: QuestionCard.Pressed) -> None:
        message.stop()
        await self._apply_choice(message.index)

    def _toggle(self, index: int) -> None:
        asyncio.ensure_future(self._apply_choice(index))

    async def _apply_choice(self, index: int) -> None:
        chosen = self._chosen[self._page]
        if self._question.multi_select:
            chosen.symmetric_difference_update({index})
            if index < len(self._cards):
                card = self._cards[index]
                card.set_selected(index in chosen)
                # Choosing by number can land on a card below the fold; showing
                # what was just ticked is the whole feedback for that keypress.
                card.scroll_visible()
            self._refresh_title()
            return
        # Single choice: picking one is the whole answer, so it moves on by
        # itself rather than making the user confirm what they just pressed.
        chosen.clear()
        chosen.add(index)
        await self._advance()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "questions-other":
            self._capture_free_text()
            await self._advance()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        self._capture_free_text()
        if event.button.id == "questions-previous":
            await self.action_previous()
        elif event.button.id == "questions-advance":
            await self._advance()
        else:
            self._submit()

    def _capture_free_text(self) -> None:
        try:
            self._other[self._page] = self.query_one("#questions-other", Input).value.strip()
        except Exception:
            pass

    async def action_previous(self) -> None:
        self._capture_free_text()
        if self._page > 0:
            self._page -= 1
            await self._render_page()

    async def action_advance(self) -> None:
        self._capture_free_text()
        await self._advance()

    async def _advance(self) -> None:
        if self._is_last:
            self._submit()
            return
        self._page += 1
        await self._render_page()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        self.dismiss(self._answers())

    def _answers(self) -> list[Answer]:
        answers: list[Answer] = []
        for index, question in enumerate(self._questions):
            labels = tuple(
                question.keyed_options[position].label
                for position in sorted(self._chosen[index])
                if position < len(question.keyed_options)
            )
            answers.append(
                Answer(question=question, chosen=labels, other=self._other[index])
            )
        return answers
