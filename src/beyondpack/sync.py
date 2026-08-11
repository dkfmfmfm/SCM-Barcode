from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .cache import CacheInfo, ProductCacheRepository
from .models import utc_now_iso
from .sources.base import ProductSource


@dataclass(frozen=True, slots=True)
class SyncResult:
    state: str
    message: str
    cache: CacheInfo
    attempted_at: str


class ProductSyncService:
    def __init__(
        self,
        source: ProductSource,
        cache: ProductCacheRepository,
        status_path: Path,
        drop_threshold: float = 0.20,
    ):
        self.source = source
        self.cache = cache
        self.status_path = status_path
        self.drop_threshold = drop_threshold

    def sync(self) -> SyncResult:
        attempted_at = utc_now_iso()
        try:
            batch = self.source.fetch_products()
            info = self.cache.replace_snapshot(batch, self.drop_threshold)
            result = SyncResult(
                "CURRENT",
                f"최신 상품정보 {info.product_count:,}건을 적용했습니다.",
                info,
                attempted_at,
            )
        except Exception as exc:
            info = self.cache.info()
            state = "CACHED" if info.product_count else "NO_DATA"
            result = SyncResult(
                state,
                f"최신 상품정보를 가져오지 못했습니다. "
                + ("기존 버전을 사용합니다." if info.product_count else "사용 가능한 캐시가 없습니다.")
                + f" ({getattr(exc, 'code', 'BP-SYNC-001')}: {exc})",
                info,
                attempted_at,
            )
        self._write_status(result)
        return result

    def _write_status(self, result: SyncResult) -> None:
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.status_path.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.status_path)
