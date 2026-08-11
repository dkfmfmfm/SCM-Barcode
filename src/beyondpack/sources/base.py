from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..models import Product


@dataclass(frozen=True, slots=True)
class ProductBatch:
    products: tuple[Product, ...]
    data_version: str
    schema_version: int


class ProductSource(ABC):
    @abstractmethod
    def fetch_products(self) -> ProductBatch:
        """Return a complete, publishable product snapshot."""
        raise NotImplementedError

