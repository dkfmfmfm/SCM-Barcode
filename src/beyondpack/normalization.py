import re
from decimal import Decimal, InvalidOperation

from .errors import PackagingValidationError


_SCANNER_WHITESPACE = re.compile(r"[\t\r\n ]+")


def normalize_fnsku(value: object) -> str:
    """Normalize scanner/master FNSKU input."""
    if value is None:
        return ""
    return _SCANNER_WHITESPACE.sub("", str(value)).upper()


def normalize_country_code(value: object) -> str:
    """Normalize a country code used in the composite product key."""
    if value is None:
        return ""
    return _SCANNER_WHITESPACE.sub("", str(value)).upper()


def normalize_shipment_code(value: object) -> str:
    """출고건(쉽먼트) 번호를 정규화한다. 박스번호를 이어붙이는 기준 키다."""
    if value is None:
        return ""
    return _SCANNER_WHITESPACE.sub("", str(value)).upper()


def product_lookup_key(fnsku: object, country_code: object) -> str:
    normalized_fnsku = normalize_fnsku(fnsku)
    normalized_country = normalize_country_code(country_code)
    if not normalized_fnsku or not normalized_country:
        return ""
    return f"{normalized_fnsku}|{normalized_country}"


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
