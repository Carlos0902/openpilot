def add(left: float, right: float) -> float:
    return left + right


def divide(numerator: float, denominator: float) -> float:
    if denominator == 0:
        raise ZeroDivisionError("division by zero")
    return numerator / denominator
