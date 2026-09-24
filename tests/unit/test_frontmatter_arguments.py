from __future__ import annotations

from code_ai.util import jsonc
from code_ai.util.arguments import (
    CLAUDE_CODE,
    CODEX,
    OPENCODE,
    fill_arguments,
    split_arguments,
)
from code_ai.util.frontmatter import as_list, split_frontmatter


def test_frontmatter_reads_lists_maps_and_block_scalars() -> None:
    front, body = split_frontmatter(
        "\ufeff---\r\nname: review\r\ndescription: >-\r\n  Reviews code\r\n  carefully.\r\n"
        "paths:\r\n  - \"src/**\"\r\n  - lib/**\r\ntools: [Read, 'Grep']\r\n"
        "permission:\r\n  edit: deny\r\n  \"*\": ask\r\n---\r\nBody.\r\n"
    )

    assert front == {
        "name": "review",
        "description": "Reviews code carefully.",
        "paths": ["src/**", "lib/**"],
        "tools": ["Read", "Grep"],
        "permission": {"edit": "deny", "*": "ask"},
    }
    assert body == "Body."


def test_frontmatter_keeps_text_it_does_not_understand() -> None:
    front, body = split_frontmatter("---\ndescription: Fix issue #12\n---\n")

    assert front == {"description": "Fix issue #12"}
    assert body == ""
    assert split_frontmatter("No frontmatter.") == ({}, "No frontmatter.")


def test_as_list_accepts_every_spelling() -> None:
    assert as_list("Read, Grep") == ["Read", "Grep"]
    assert as_list("Read Grep") == ["Read", "Grep"]
    assert as_list(["Read"]) == ["Read"]
    assert as_list("") == []


def test_quotes_group_words_and_backslashes_survive() -> None:
    assert split_arguments(r'"hello world" C:\src\app.py') == ["hello world", r"C:\src\app.py"]


def test_claude_placeholders_are_zero_based_and_named() -> None:
    text, used = fill_arguments(
        "$ARGUMENTS | $0 $1 | $ARGUMENTS[1] | $issue | \\$1.00 | $9",
        "12 main",
        dialect=CLAUDE_CODE,
        names=["issue"],
    )

    assert text == "12 main | 12 main | main | 12 | $1.00 | $9"
    assert used


def test_codex_placeholders_are_one_based_with_key_pairs() -> None:
    text, used = fill_arguments("$1 $2 $TICKET $$ $OTHER", "a b TICKET=42", dialect=CODEX)

    assert text == "a b 42 $ $OTHER"
    assert used


def test_opencode_placeholders_and_untouched_text() -> None:
    assert fill_arguments("Run $1 ($ARGUMENTS)", "x y", dialect=OPENCODE) == (
        "Run x (x y)",
        True,
    )
    assert fill_arguments("No placeholders. $5", "x", dialect=OPENCODE) == (
        "No placeholders. $5",
        False,
    )
    assert fill_arguments("$1", "x", dialect="") == ("$1", False)


def test_jsonc_accepts_comments_and_trailing_commas() -> None:
    assert jsonc.loads('{\n  // note\n  "a": "http://x", /* b */ "c": [1, 2,],\n}') == {
        "a": "http://x",
        "c": [1, 2],
    }
