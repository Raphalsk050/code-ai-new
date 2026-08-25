from __future__ import annotations

from pathlib import Path

from code_ai.sandbox.layout import SandboxLayout
from code_ai.sandbox.runtimes import (
    DEFAULT_RUNTIMES,
    GenericRuntime,
    PythonRuntime,
    RuntimeScratch,
    build_runtime_scratch,
)


def make_layout() -> SandboxLayout:
    return SandboxLayout.under(Path("/base"), "session")


def test_every_redirected_path_stays_inside_the_sandbox() -> None:
    layout = make_layout()

    scratch = build_runtime_scratch(layout, {})

    for value in scratch.variables.values():
        if value.startswith("/"):
            assert Path(value).is_relative_to(layout.root), value
    for directory in scratch.directories:
        assert directory.is_relative_to(layout.root)


def test_temp_is_redirected_in_both_posix_and_windows_spellings() -> None:
    layout = make_layout()

    scratch = GenericRuntime().scratch(layout, {})

    assert scratch.variables["TMPDIR"] == str(layout.tmp)
    assert scratch.variables["TEMP"] == str(layout.tmp)
    assert scratch.variables["TMP"] == str(layout.tmp)


def test_python_disables_the_in_tree_pytest_cache() -> None:
    scratch = PythonRuntime().scratch(make_layout(), {})

    assert scratch.variables["PYTEST_ADDOPTS"] == "-p no:cacheprovider"


def test_python_appends_to_an_existing_addopts_instead_of_replacing_it() -> None:
    scratch = PythonRuntime().scratch(make_layout(), {"PYTEST_ADDOPTS": "-x --tb=short"})

    assert scratch.variables["PYTEST_ADDOPTS"] == "-x --tb=short -p no:cacheprovider"


def test_an_addopts_that_already_disables_the_cache_is_left_alone() -> None:
    scratch = PythonRuntime().scratch(make_layout(), {"PYTEST_ADDOPTS": "-p no:cacheprovider -q"})

    assert scratch.variables["PYTEST_ADDOPTS"] == "-p no:cacheprovider -q"


def test_default_runtimes_cover_the_common_toolchains() -> None:
    names = {runtime.name for runtime in DEFAULT_RUNTIMES}

    assert {"generic", "python", "node", "rust", "go"} <= names


def test_bulky_build_outputs_are_redirected() -> None:
    variables = build_runtime_scratch(make_layout(), {}).variables

    assert "CARGO_TARGET_DIR" in variables
    assert "GOCACHE" in variables
    assert "npm_config_cache" in variables


def test_a_later_runtime_refines_the_generic_one() -> None:
    class Overriding:
        name = "overriding"

        def scratch(self, layout, base) -> RuntimeScratch:
            return RuntimeScratch(variables={"TMPDIR": "/elsewhere"})

    variables = build_runtime_scratch(
        make_layout(), {}, (GenericRuntime(), Overriding())
    ).variables

    assert variables["TMPDIR"] == "/elsewhere"


def test_directories_are_deduplicated_in_creation_order() -> None:
    layout = make_layout()

    directories = build_runtime_scratch(layout, {}).directories

    assert len(directories) == len(set(directories))
    assert directories.index(layout.tmp) < directories.index(layout.tmp / "pytest")
