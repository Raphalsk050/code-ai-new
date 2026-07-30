from __future__ import annotations

import json

from code_ai.util.partial_json import PartialObjectDecoder


def _decode(raw: str, keys=None) -> PartialObjectDecoder:
    decoder = PartialObjectDecoder(keys)
    decoder.feed(raw)
    return decoder


# --- complete documents ------------------------------------------------------


def test_decodes_top_level_strings() -> None:
    decoder = _decode('{"path": "a.py", "content": "print(1)"}')
    assert decoder.text("path") == "a.py"
    assert decoder.text("content") == "print(1)"
    assert decoder.value("content").closed is True


def test_decodes_escapes() -> None:
    raw = json.dumps({"content": 'line\n\t"quoted"\\ / done'})
    assert _decode(raw).text("content") == 'line\n\t"quoted"\\ / done'


def test_decodes_unicode_escapes_and_surrogate_pairs() -> None:
    raw = json.dumps({"content": "café 🚀"}, ensure_ascii=True)
    # ensure_ascii escapes the emoji as a 🚀 surrogate pair.
    assert "\\ud83d" in raw.lower()
    assert _decode(raw).text("content") == "café 🚀"


def test_ignores_nested_and_non_string_members() -> None:
    decoder = _decode(
        '{"path": "a.py", "meta": {"path": "nested.py", "n": 1}, "flag": true,'
        ' "list": ["x"], "content": "body"}'
    )
    assert decoder.text("path") == "a.py"
    assert decoder.text("content") == "body"


def test_keys_filter_retains_only_requested_values() -> None:
    decoder = _decode('{"path": "a.py", "content": "body"}', keys=("content",))
    assert decoder.text("content") == "body"
    assert decoder.started("path") is False


# --- truncated documents (the streaming case) --------------------------------


def test_open_value_is_readable_but_not_closed() -> None:
    decoder = _decode('{"path": "a.py", "content": "def main(')
    assert decoder.text("path") == "a.py"
    assert decoder.value("path").closed is True
    assert decoder.text("content") == "def main("
    assert decoder.value("content").closed is False


def test_value_started_before_any_character_arrives() -> None:
    decoder = _decode('{"path": "a.py", "content": "')
    assert decoder.started("content") is True
    assert decoder.text("content") == ""


def test_missing_key_reports_not_started() -> None:
    assert _decode('{"content": "no path yet').started("path") is False


def test_truncated_escape_is_held_back_not_guessed() -> None:
    # A lone backslash could still become \n, \" or \uXXXX: emitting anything
    # now would have to be rewritten later, breaking the append-only contract.
    assert _decode('{"content": "a\\').text("content") == "a"
    assert _decode('{"content": "a\\u00e').text("content") == "a"
    assert _decode('{"content": "a\\u00e9').text("content") == "aé"


def test_truncated_high_surrogate_is_held_until_its_pair() -> None:
    assert _decode('{"content": "x\\ud83d').text("content") == "x"
    assert _decode('{"content": "x\\ud83d\\ude80').text("content") == "x🚀"


def test_unpaired_high_surrogate_falls_back_to_replacement() -> None:
    assert _decode('{"content": "x\\ud83dy"}').text("content") == "x�y"


# --- incremental feeding -----------------------------------------------------


def test_feeding_fragment_by_fragment_matches_one_shot() -> None:
    raw = json.dumps(
        {"path": "src/app.py", "content": "def f():\n    return '🚀 ok'\n"},
        ensure_ascii=True,
    )
    incremental = PartialObjectDecoder()
    for index in range(1, len(raw) + 1):
        incremental.feed(raw[:index])

    assert incremental.text("content") == _decode(raw).text("content")
    assert incremental.text("path") == "src/app.py"


def test_decoded_text_only_ever_grows() -> None:
    raw = json.dumps({"content": "a\nb\t\"c\" 🚀 d\\e"}, ensure_ascii=True)
    decoder = PartialObjectDecoder()
    previous = ""
    for index in range(1, len(raw) + 1):
        decoder.feed(raw[:index])
        current = decoder.text("content")
        assert current.startswith(previous)
        previous = current


def test_shrinking_buffer_is_ignored() -> None:
    decoder = PartialObjectDecoder()
    decoder.feed('{"content": "abc')
    decoder.feed('{"content": "a')
    assert decoder.text("content") == "abc"


# --- lenient behaviour -------------------------------------------------------


def test_first_started_picks_the_streaming_key() -> None:
    decoder = _decode('{"path": "a.py", "old_text": "x", "new_text": "y')
    assert decoder.first_started(("content", "new_text")) == "new_text"
    assert decoder.first_started(("content",)) == ""


def test_unknown_escape_passes_through() -> None:
    assert _decode('{"content": "a\\q"}').text("content") == "aq"


def test_invalid_unicode_escape_is_shown_literally() -> None:
    assert _decode('{"content": "a\\uZZZZ"}').text("content") == "a\\uZZZZ"
