from __future__ import annotations

import json
from pathlib import Path

from ..errors import SourceError
from .base import ProductBatch, ProductSource
from .mapping import map_product


class JsonProductSource(ProductSource):
    def __init__(self, path: Path):
        self.path = path

    def fetch_products(self) -> ProductBatch:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SourceError(f"상품 JSON을 읽을 수 없습니다: {self.path}") from exc
        if isinstance(payload, list):
            rows, version, schema = payload, "json-local", 1
        elif isinstance(payload, dict):
            rows = payload.get("products", [])
            version = str(payload.get("data_version", "json-local"))
            schema = int(payload.get("schema_version", 1))
        else:
            raise SourceError("상품 JSON 최상위 값은 객체 또는 배열이어야 합니다.")
        if not isinstance(rows, list):
            raise SourceError("products는 배열이어야 합니다.")
        return ProductBatch(
            products=tuple(map_product(row, version, schema) for row in rows if isinstance(row, dict)),
            data_version=version,
            schema_version=schema,
        )
