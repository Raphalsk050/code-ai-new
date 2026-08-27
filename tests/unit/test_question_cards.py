"""Driving the question dialog the way a user does: pressing cards and keys."""

from __future__ import annotations

from pathlib import Path

from textual.app import App

from code_ai.core.interaction import Answer, Question, Questionnaire
from code_ai.ui.terminal.questions import QuestionCard, QuestionnaireModal

THEME = Path("src/code_ai/ui/terminal/theme.tcss").resolve()


def text_of(modal, selector: str) -> str:
    """Plain text a Static is currently showing, whatever it was updated with."""

    content = modal.query_one(selector).content
    return getattr(content, "plain", content)


def question(prompt: str, **overrides) -> Question:
    payload = {
        "question": prompt,
        "header": overrides.pop("header", "Banco"),
        "options": overrides.pop(
            "options", ["Postgres :: consistência forte", "SQLite :: zero configuração"]
        ),
    }
    payload.update(overrides)
    return Question.from_payload(payload)


class Harness(App):
    CSS_PATH = str(THEME)

    def __init__(self, questionnaire: Questionnaire) -> None:
        super().__init__()
        self._questionnaire = questionnaire
        self.answers: list[Answer] | None | str = "unset"

    async def on_mount(self) -> None:
        def capture(result) -> None:
            self.answers = result

        await self.push_screen(QuestionnaireModal(self._questionnaire), capture)


async def test_pressing_a_card_answers_and_moves_on() -> None:
    app = Harness(
        Questionnaire(questions=(question("Qual banco?"), question("Como autenticar?")))
    )

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, QuestionnaireModal)
        # First page, first card.
        await pilot.click(modal.query(QuestionCard).first())
        await pilot.pause()
        # A single-choice question does not make the user confirm what they
        # just pressed: it advances by itself.
        assert "Pergunta 2 de 2" in text_of(modal, "#questions-title")
        await pilot.click(modal.query(QuestionCard).first())
        await pilot.pause()

    assert [answer.chosen for answer in app.answers] == [("Postgres",), ("Postgres",)]


async def test_a_number_key_chooses_the_matching_card() -> None:
    app = Harness(Questionnaire(questions=(question("Qual banco?"),)))

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()

    assert app.answers[0].chosen == ("SQLite",)


async def test_the_title_numbers_and_counts_the_pages() -> None:
    app = Harness(
        Questionnaire(
            questions=(
                question("Qual banco?", header="Banco"),
                question("Como autenticar?", header="Auth"),
                question("Qual prazo?", header="Prazo"),
            )
        )
    )

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        modal = app.screen
        title = text_of(modal, "#questions-title")
        assert "Pergunta 1 de 3" in title
        assert "Banco" in title
        # A dot per page, with the current one marked.
        assert "◉ ○ ○" in title
        await pilot.press("right")
        await pilot.pause()
        assert "Pergunta 2 de 3 · Auth" in text_of(modal, "#questions-title")
        await pilot.press("left")
        await pilot.pause()
        assert "Pergunta 1 de 3" in text_of(modal, "#questions-title")
        modal.dismiss(None)
        await pilot.pause()


async def test_a_single_question_is_not_labelled_as_a_page_of_one() -> None:
    app = Harness(Questionnaire(questions=(question("Qual banco?"),)))

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        title = text_of(app.screen, "#questions-title")
        assert "Pergunta 1 de 1" not in title
        assert "Banco" in title
        app.screen.dismiss(None)
        await pilot.pause()


async def test_several_options_can_be_chosen_when_the_question_allows_it() -> None:
    app = Harness(
        Questionnaire(questions=(question("Quais bancos?", multi_select=True),))
    )

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        modal = app.screen
        await pilot.press("1")
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()
        # Multi-select does not advance on a press, so both are still on the page.
        assert "Pergunta" not in text_of(modal, "#questions-title")
        modal._submit()
        await pilot.pause()

    assert app.answers[0].chosen == ("Postgres", "SQLite")


async def test_choosing_the_same_option_twice_unchooses_it() -> None:
    app = Harness(
        Questionnaire(questions=(question("Quais bancos?", multi_select=True),))
    )

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        app.screen._submit()
        await pilot.pause()

    assert app.answers[0].chosen == ()


async def test_a_typed_answer_is_kept_alongside_the_cards() -> None:
    app = Harness(Questionnaire(questions=(question("Qual banco?"),)))

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        modal = app.screen
        modal.query_one("#questions-other").focus()
        await pilot.pause()
        await pilot.press(*"MongoDB")
        await pilot.press("enter")
        await pilot.pause()

    assert app.answers[0].other == "MongoDB"
    assert app.answers[0].chosen == ()


async def test_a_question_with_no_options_offers_the_text_field_only() -> None:
    app = Harness(Questionnaire(questions=(question("Qual o prazo?", options=[]),)))

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        modal = app.screen
        assert modal.query(QuestionCard).__len__() == 0
        assert modal.query_one("#questions-other").display is True
        assert modal.query_one("#questions-cards").display is False
        modal.dismiss(None)
        await pilot.pause()


async def test_closing_the_dialog_leaves_the_questions_answerable_by_typing() -> None:
    app = Harness(Questionnaire(questions=(question("Qual banco?"),)))

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

    # None, not an empty answer list: escaping is a "not now", not a reply.
    assert app.answers is None


async def test_the_key_hints_match_what_the_page_actually_accepts() -> None:
    app = Harness(
        Questionnaire(
            questions=(
                question("Quais bancos?", multi_select=True),
                question("Qual prazo?", options=[]),
            )
        )
    )

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        modal = app.screen
        assert "[1-9] escolher" in text_of(modal, "#questions-keys")
        assert "[Enter] confirmar" in text_of(modal, "#questions-keys")
        await pilot.press("right")
        await pilot.pause()
        # Nothing to choose on a free-text page, so no number hint.
        assert "[1-9] escolher" not in text_of(modal, "#questions-keys")
        modal.dismiss(None)
        await pilot.pause()
