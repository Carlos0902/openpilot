import pytest

from calculator import add, divide


def test_add() -> None:
    assert add(2, 3) == 5


def test_divide() -> None:
    assert divide(8, 2) == 4


def test_divide_by_zero_has_domain_error() -> None:
    with pytest.raises(ValueError, match="denominator"):
        divide(8, 0)
