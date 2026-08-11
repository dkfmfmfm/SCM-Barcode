from __future__ import annotations

from typing import Any

from ..models import Product


FIELD_ALIASES = {
    "fnsku": ("FNSKU", "fnsku"),
    "item_code": ("ItemCode", "item_code", "productCode", "product_code"),
    "sku": ("SKU", "sku"),
    "country_code": ("CountryCode", "country_code", "country"),
    "country_name": ("CountryName", "country_name"),
    "product_name": ("ProductName", "product_name", "nameKr", "name_kr", "nameEn", "name_en"),
    "product_name_en": ("ProductNameEn", "product_name_en", "nameEn", "name_en"),
    "amazon_account": ("AmazonAccount", "amazon_account"),
    "status": ("Status", "status", "active"),
    "source_modified_at": ("SourceModifiedAt", "source_modified_at", "modified"),
    "data_version": ("DataVersion", "data_version"),
    "schema_version": ("SchemaVersion", "schema_version"),
    "lookup_key": ("LookupKey", "lookup_key"),
}


def _pick(row: dict[str, Any], name: str, default: Any = "") -> Any:
    for key in FIELD_ALIASES[name]:
        if key in row and row[key] is not None:
            return row[key]
    return default


def map_product(row: dict[str, Any], default_version: str = "", default_schema: int = 1) -> Product:
    raw_status = str(_pick(row, "status", "Published")).strip()
    status = "Published" if raw_status.upper() in {"Y", "PUBLISHED", "ACTIVE"} else raw_status
    country_code = str(_pick(row, "country_code")).strip().upper()
    country_name = str(_pick(row, "country_name")).strip() or country_code
    try:
        schema_version = int(_pick(row, "schema_version", default_schema) or default_schema)
    except (TypeError, ValueError):
        schema_version = -1
    return Product(
        fnsku=str(_pick(row, "fnsku")).strip(),
        item_code=str(_pick(row, "item_code")).strip(),
        sku=str(_pick(row, "sku")).strip(),
        country_code=country_code,
        country_name=country_name,
        product_name=str(_pick(row, "product_name")).strip(),
        product_name_en=str(_pick(row, "product_name_en")).strip(),
        amazon_account=str(_pick(row, "amazon_account")).strip(),
        status=status,
        source_modified_at=str(_pick(row, "source_modified_at")).strip(),
        data_version=str(_pick(row, "data_version", default_version) or default_version).strip(),
        schema_version=schema_version,
        lookup_key=str(_pick(row, "lookup_key")).strip().upper(),
    )
