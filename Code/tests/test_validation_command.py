import pytest

from core.validation_command import normalize_command_argv, validation_commands_match


def test_normalize_command_argv_accepts_whitespace_and_quoting_variants() -> None:
    expected = "python -m pytest -q tests/test_calculator.py"

    assert validation_commands_match(expected, "  python   -m pytest -q 'tests/test_calculator.py'  ")


@pytest.mark.parametrize(
    "command",
    [
        "cd /project && python -m pytest -q tests/test_calculator.py",
        "python -m pytest -q tests/test_calculator.py && echo extra",
        "python -m pytest -q tests/test_calculator.py; echo extra",
        "python -m pytest -q tests/test_calculator.py | tee result.txt",
        "python -m pytest -q tests/test_calculator.py > result.txt",
        "python -m pytest -q tests/test_calculator.py $(echo extra)",
    ],
)
def test_validation_command_match_does_not_grant_shell_wrapper_equivalence(command: str) -> None:
    expected = "python -m pytest -q tests/test_calculator.py"

    assert validation_commands_match(expected, command) is False


def test_normalize_command_argv_fails_closed_for_unbalanced_quotes() -> None:
    assert normalize_command_argv("python -m pytest 'tests/test_calculator.py") is None
