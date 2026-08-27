"""Driving the question dialog the way a user does: pressing cards and keys."""

from __future__ import annotations

from pathlib import Path

from textual.app import App

from code_ai.core.interaction import Answer, Question, Questionnaire
from code_ai.ui.terminal.app import create_terminal_app
from code_ai.ui.terminal.questions import QuestionCard, QuestionnaireModal
from tests.unit.test_terminal_ui import FakeTerminalApplication

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


# ------------------------------------------------------- inside the real TUI


async def ask_two_questions(fake_app) -> None:
    """Replay what a turn ending on two ask_user calls actually emits."""

    await fake_app.emit("status.changed", {"state": "EXECUTING_TOOL"})
    await fake_app.emit(
        "interaction.question.requested",
        {
            "question": "Qual banco de dados?",
            "header": "Banco",
            "options": ["Postgres :: consistência forte", "SQLite"],
            "why_required": "Sem isso não dá para escolher o schema.",
        },
    )
    await fake_app.emit(
        "interaction.question.requested",
        {"question": "Como autenticar?", "header": "Auth", "options": ["OAuth", "Senha"]},
    )
    await fake_app.emit("status.changed", {"state": "READY"})


async def test_the_cards_open_when_the_turn_ends_and_send_one_reply(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    fake_app.answered = []

    async def submit_question_answer(text: str) -> None:
        fake_app.answered.append(text)

    fake_app.submit_question_answer = submit_question_answer
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        await ask_two_questions(fake_app)
        await pilot.pause()

        modal = terminal_app.screen
        assert isinstance(modal, QuestionnaireModal)
        assert "Pergunta 1 de 2 · Banco" in text_of(modal, "#questions-title")
        assert "Sem isso não dá" in text_of(modal, "#questions-why")

        await pilot.press("1")
        await pilot.pause()
        assert "Pergunta 2 de 2 · Auth" in text_of(terminal_app.screen, "#questions-title")
        await pilot.press("2")
        await pilot.pause(0.2)

    # One message, each line naming the question it answers.
    assert fake_app.answered == ["1. Banco: Postgres\n2. Auth: Senha"]


async def test_the_cards_do_not_open_while_the_agent_is_still_working(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        await fake_app.emit("status.changed", {"state": "EXECUTING_TOOL"})
        await fake_app.emit(
            "interaction.question.requested", {"question": "Qual banco?", "options": ["A"]}
        )
        await pilot.pause()

        # A dialog over a moving transcript would hide the question's own text.
        assert not isinstance(terminal_app.screen, QuestionnaireModal)


async def test_closing_the_cards_sends_nothing(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    fake_app.answered = []
    fake_app.submit_question_answer = lambda text: fake_app.answered.append(text)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        await ask_two_questions(fake_app)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert fake_app.answered == []
        # And it does not spring back open on the next event.
        await fake_app.emit("status.changed", {"state": "READY"})
        await pilot.pause()
        assert not isinstance(terminal_app.screen, QuestionnaireModal)


async def test_a_new_user_message_drops_a_question_that_was_never_answered(tmp_path) -> None:
    fake_app = FakeTerminalApplication(tmp_path)
    terminal_app = create_terminal_app(fake_app)

    async with terminal_app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        await fake_app.emit("status.changed", {"state": "EXECUTING_TOOL"})
        await fake_app.emit(
            "interaction.question.requested", {"question": "Qual banco?", "options": ["A"]}
        )
        # The user types something else instead of answering.
        await fake_app.emit("user.message", {"text": "deixa pra lá, faz outra coisa"})
        await fake_app.emit("status.changed", {"state": "READY"})
        await pilot.pause()

        assert not isinstance(terminal_app.screen, QuestionnaireModal)
