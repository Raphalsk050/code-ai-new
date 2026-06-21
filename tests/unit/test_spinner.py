from __future__ import annotations

from code_ai.ui.terminal.widgets import (
    CODE_AI_SPINNER_OPTIONS,
    DEFAULT_SPINNER,
    WORKING_SPINNERS,
    WORKING_STATES,
    normalize_spinner,
    resolve_spinner,
    spinner_color,
    working_label,
)


def test_default_spinner_is_registered_and_first() -> None:
    assert DEFAULT_SPINNER in WORKING_SPINNERS
    assert CODE_AI_SPINNER_OPTIONS[0] == DEFAULT_SPINNER


def test_every_spinner_has_frames_and_positive_interval() -> None:
    for style in WORKING_SPINNERS.values():
        assert style.frames, f"{style.key} has no frames"
        assert style.interval > 0
        assert style.key == normalize_spinner(style.key)


def test_normalize_spinner_falls_back_to_default() -> None:
    assert normalize_spinner("not-a-real-spinner") == DEFAULT_SPINNER
    assert normalize_spinner("  star-spin  ") == "star-spin"


def test_resolve_spinner_returns_style_object() -> None:
    style = resolve_spinner("garbage")
    assert style.key == DEFAULT_SPINNER
    assert resolve_spinner("ascii").frames == ("|", "/", "—", "\\")


def test_spinner_color_is_valid_hex_and_wraps() -> None:
    for progress in (0.0, 0.25, 0.5, 0.9, 1.0, 2.3):
        color = spinner_color(progress)
        assert color.startswith("#") and len(color) == 7
        int(color[1:], 16)  # raises if not hex
    # progress wraps at 1.0, so 0.0 and 1.0 land on the same color.
    assert spinner_color(0.0) == spinner_color(1.0)


def test_working_label_maps_known_states() -> None:
    assert working_label("CALLING_MODEL") == "calling model"
    assert working_label("EXECUTING_TOOL") == "running tools"
    assert working_label("READY") == "working"


def test_working_states_are_active_only() -> None:
    assert "READY" not in WORKING_STATES
    assert "STARTING" not in WORKING_STATES
    assert "CALLING_MODEL" in WORKING_STATES
