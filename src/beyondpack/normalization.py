import re
from decimal import Decimal, InvalidOperation

from .errors import PackagingValidationError


_SCANNER_WHITESPACE = re.compile(r"[\t\r\n ]+")


def normalize_fnsku(value: object) -> str:
    """Normalize scanner/master input to the unique local lookup key."""
    if value is None:
        return ""
    return _SCANNER_WHITESPACE.sub("", str(value)).upper()


def positive_int(value: object, label: str) -> int:
    try:
        text = str(value).strip()
        if not re.fullmatch(r"\d+", text):
            raise ValueError
        parsed = int(text)
    except (TypeError, ValueError):
        raise PackagingValidationError(f"{label}은(는) 1 이상의 정수여야 합니다.")
    if parsed < 1:
        raise PackagingValidationError(f"{label}은(는) 1 이상이어야 합니다.")
    return parsed


def positive_decimal(value: object, label: str, max_value: Decimal | None = None) -> Decimal:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise PackagingValidationError(f"{label}은(는) 0보다 큰 숫자여야 합니다.")
    if not parsed.is_finite() or parsed <= 0:
        raise PackagingValidationError(f"{label}은(는) 0보다 커야 합니다.")
    if max_value is not None and parsed > max_value:
        raise PackagingValidationError(f"{label}은(는) {max_value} 이하여야 합니다.")
    return parsed

