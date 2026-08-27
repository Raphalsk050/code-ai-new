from __future__ import annotations

from code_ai.core.interaction import (
    Answer,
    Question,
    Questionnaire,
    QuestionOption,
    render_answers,
)


def make_question(**overrides) -> Question:
    payload = {
        "question": "Qual banco de dados o sistema deve usar?",
        "header": "Banco de dados",
        "options": ["Postgres :: relacional, consistência forte", "SQLite :: zero configuração"],
    }
    payload.update(overrides)
    return Question.from_payload(payload)


# ------------------------------------------------------------------ options


def test_an_option_carries_its_reason_when_one_is_given() -> None:
    option = QuestionOption.parse("Postgres :: relacional, consistência forte")

    assert option.label == "Postgres"
    assert option.description == "relacional, consistência forte"


def test_an_option_without_a_reason_is_just_a_label() -> None:
    option = QuestionOption.parse("  Postgres  ")

    assert option == QuestionOption(label="Postgres")


def test_an_empty_option_is_dropped_rather_than_shown_as_a_blank_card() -> None:
    assert QuestionOption.parse("   ") is None
    assert QuestionOption.parse(":: só a descrição") is None


def test_a_blank_option_in_the_list_does_not_become_a_card() -> None:
    question = make_question(options=["Postgres", "", "   ", "SQLite"])

    assert [option.label for option in question.options] == ["Postgres", "SQLite"]


# ---------------------------------------------------------------- questions


def test_a_question_needs_a_prompt() -> None:
    assert Question.from_payload({"question": "   "}) is None
    assert Question.from_payload({}) is None


def test_the_card_title_falls_back_to_the_prompt_when_no_header_is_given() -> None:
    question = make_question(header="")

    assert question.title.startswith("Qual banco de dados")
    assert len(question.title) <= 32


def test_only_the_first_nine_options_get_a_number_key() -> None:
    question = make_question(options=[f"opção {n}" for n in range(1, 13)])

    assert len(question.options) == 12
    assert len(question.keyed_options) == 9


def test_a_question_with_no_options_always_accepts_free_text() -> None:
    # Refusing free text on a question with nothing to click would leave the
    # user with no way to answer at all.
    question = Question.from_payload({"question": "Qual o prazo?", "allow_other": False})

    assert question.options == ()
    assert question.allow_other is True


def test_free_text_can_be_refused_when_there_are_options_to_pick() -> None:
    question = make_question(allow_other=False)

    assert question.allow_other is False


def test_a_question_round_trips_through_its_payload() -> None:
    question = make_question(multi_select=True)

    rebuilt = Question.from_payload(question.to_payload())

    assert rebuilt == question


# ----------------------------------------------------------- questionnaire


def test_questions_that_could_not_be_read_are_left_out() -> None:
    questionnaire = Questionnaire.from_payloads(
        [{"question": "Primeira?"}, {"question": ""}, {"question": "Segunda?"}]
    )

    assert len(questionnaire) == 2
    assert [q.prompt for q in questionnaire] == ["Primeira?", "Segunda?"]


def test_a_single_question_is_not_dressed_up_as_a_list() -> None:
    text = Questionnaire(questions=(make_question(),)).render_text()

    assert "Preciso de" not in text
    assert "Pergunta 1 de 1" not in text
    assert "[1] Postgres - relacional, consistência forte" in text


def test_several_questions_are_numbered_and_counted(tmp_path) -> None:
    questionnaire = Questionnaire(
        questions=(
            make_question(),
            make_question(question="Autenticação?", header="Auth", options=["OAuth", "Senha"]),
        )
    )

    text = questionnaire.render_text()

    assert "Preciso de 2 respostas" in text
    assert "Pergunta 1 de 2 · Banco de dados" in text
    assert "Pergunta 2 de 2 · Auth" in text
    # Options restart at 1 on each question, so an answer of "2" is unambiguous
    # only together with the question it belongs to.
    assert text.count("[1]") == 2
    assert text.count("[2]") == 2


def test_the_text_form_says_how_to_answer(tmp_path) -> None:
    single = Questionnaire(questions=(make_question(),)).render_text()
    multi = Questionnaire(questions=(make_question(multi_select=True),)).render_text()

    assert "responda pelo número" in single
    assert "separados por vírgula" in multi
    assert "escreva a sua resposta" in single


def test_a_questionnaire_with_options_only_omits_the_free_text_hint() -> None:
    text = Questionnaire(questions=(make_question(allow_other=False),)).render_text()

    assert "escreva a sua resposta" not in text


def test_an_empty_questionnaire_renders_to_nothing() -> None:
    assert Questionnaire().render_text() == ""
    assert Questionnaire().is_empty is True


# --------------------------------------------------------------- answers


def test_an_answer_reads_as_the_choice_that_was_made() -> None:
    answer = Answer(question=make_question(), chosen=("Postgres",))

    assert answer.render() == "Postgres"


def test_several_choices_are_joined() -> None:
    answer = Answer(question=make_question(multi_select=True), chosen=("Postgres", "SQLite"))

    assert answer.render() == "Postgres, SQLite"


def test_free_text_alongside_a_choice_is_kept_as_an_addition() -> None:
    answer = Answer(question=make_question(), chosen=("Postgres",), other="mas em RDS")

    assert answer.render() == "Postgres, além disso: mas em RDS"


def test_free_text_on_its_own_stands_alone() -> None:
    answer = Answer(question=make_question(), other="MongoDB")

    assert answer.render() == "MongoDB"


def test_the_reply_repeats_each_question_so_the_model_cannot_mix_them_up() -> None:
    questions = (
        make_question(),
        make_question(question="Autenticação?", header="Auth", options=["OAuth", "Senha"]),
    )
    reply = render_answers(
        [
            Answer(question=questions[0], chosen=("Postgres",)),
            Answer(question=questions[1], chosen=("OAuth",)),
        ]
    )

    assert reply == "1. Banco de dados: Postgres\n2. Auth: OAuth"


def test_a_question_left_blank_is_left_out_of_the_reply() -> None:
    questions = (make_question(), make_question(question="Autenticação?", header="Auth"))
    reply = render_answers(
        [Answer(question=questions[0], chosen=("Postgres",)), Answer(question=questions[1])]
    )

    assert reply == "1. Banco de dados: Postgres"


def test_nothing_answered_produces_no_message() -> None:
    assert render_answers([Answer(question=make_question())]) == ""
