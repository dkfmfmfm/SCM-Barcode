from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from typing import Any

from ..errors import SourceError
from ..models import Product
from .base import ProductBatch
from .mapping import map_product


REQUIRED_HEADERS = {
    "FNSKU",
    "ItemCode",
    "SKU",
    "CountryCode",
    "CountryName",
    "ProductName",
}


def rows_to_batch(
    rows: Iterable[Mapping[str, Any]],
    *,
    source_name: str,
    content_fingerprint: bytes,
) -> ProductBatch:
    materialized = [
        {str(key).strip().lstrip("\ufeff"): value for key, value in row.items() if key is not None}
        for row in rows
        if any(str(value or "").strip() for value in row.values())
    ]
    if not materialized:
        raise SourceError(f"{source_name}에 상품 행이 없습니다.")

    headers = set(materialized[0])
    missing_headers = sorted(REQUIRED_HEADERS - headers)
    if missing_headers:
        raise SourceError(
            f"{source_name} 필수 열이 없습니다: {', '.join(missing_headers)}. "
            "첫 행에 BeyondPack_Master 열 제목을 넣으세요."
        )

    versions = {
        str(row.get("DataVersion", "")).strip()
        for row in materialized
        if str(row.get("DataVersion", "")).strip()
    }
    if len(versions) > 1:
        raise SourceError(f"{source_name}의 DataVersion이 2개 이상입니다.")
    version = next(iter(versions), "") or (
        "AUTO-" + hashlib.sha256(content_fingerprint).hexdigest()[:12].upper()
    )

    schemas: set[int] = set()
    for row in materialized:
        value = str(row.get("SchemaVersion", "")).strip()
        if not value:
            continue
        try:
            schemas.add(int(value))
        except ValueError as exc:
            raise SourceError(f"{source_name} SchemaVersion은 숫자여야 합니다: {value}") from exc
    if len(schemas) > 1:
        raise SourceError(f"{source_name}의 SchemaVersion이 2개 이상입니다.")
    schema = next(iter(schemas), 2)

    products: list[Product] = []
    for row_number, row in enumerate(materialized, start=2):
        mapped = dict(row)
        mapped.setdefault("DataVersion", version)
        mapped.setdefault("SchemaVersion", schema)
        mapped.setdefault("Status", "Published")
        product = map_product(mapped, version, schema)
        if not product.lookup_key:
            values = product.to_dict()
            values["lookup_key"] = product.computed_lookup_key
            product = Product(**values)
        products.append(product)
        if not product.normalized_fnsku:
            raise SourceError(f"{source_name} {row_number}행의 FNSKU가 비어 있습니다.")

    return ProductBatch(tuple(products), version, schema)
