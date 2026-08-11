from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .normalization import normalize_country_code, normalize_fnsku, product_lookup_key


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True, slots=True)
class Product:
    fnsku: str
    item_code: str
    sku: str
    country_code: str
    country_name: str
    product_name: str
    product_name_en: str = ""
    amazon_account: str = ""
    status: str = "Published"
    source_modified_at: str = ""
    data_version: str = ""
    schema_version: int = 2
    lookup_key: str = ""

    @property
    def normalized_fnsku(self) -> str:
        return normalize_fnsku(self.fnsku)

    @property
    def normalized_country_code(self) -> str:
        return normalize_country_code(self.country_code)

    @property
    def computed_lookup_key(self) -> str:
        return product_lookup_key(self.fnsku, self.country_code)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BoxItem:
    fnsku: str
    item_code: str
    sku: str
    country_code: str
    country_name: str
    product_name: str
    qty_per_box: int
    source_modified_at: str = ""

    @classmethod
    def from_product(cls, product: Product, qty_per_box: int) -> "BoxItem":
        return cls(
            fnsku=product.normalized_fnsku,
            item_code=product.item_code,
            sku=product.sku,
            country_code=product.country_code,
            country_name=product.country_name,
            product_name=product.product_name,
            qty_per_box=qty_per_box,
            source_modified_at=product.source_modified_at,
        )


@dataclass(frozen=True, slots=True)
class BoxGroupInput:
    box_count: int
    weight_kg: Decimal
    length_cm: Decimal
    width_cm: Decimal
    height_cm: Decimal
    items: tuple[BoxItem, ...]
