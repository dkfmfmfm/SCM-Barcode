from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from ..config import SharePointSettings
from ..errors import AuthenticationError, ConfigurationError, SourceError
from ..token_cache import SecureTokenStore
from .base import ProductBatch, ProductSource
from .mapping import map_product


GRAPH_FIELDS = (
    "FNSKU,ItemCode,SKU,CountryCode,CountryName,ProductName,ProductNameEn,"
    "AmazonAccount,Status,SourceModifiedAt,DataVersion,SchemaVersion"
)


class SharePointProductSource(ProductSource):
    def __init__(
        self,
        settings: SharePointSettings,
        token_path: Path,
        login_notifier: Callable[[str], None] | None = None,
        timeout_seconds: int = 20,
    ):
        self.settings = settings
        self.token_store = SecureTokenStore(token_path)
        self.login_notifier = login_notifier or (lambda message: None)
        self.timeout_seconds = timeout_seconds
        self._validate_settings()

    def _validate_settings(self) -> None:
        missing = [
            name
            for name in ("tenant_id", "client_id", "site_id", "list_id")
            if not getattr(self.settings, name)
        ]
        if missing:
            raise ConfigurationError("SharePoint 설정 누락: " + ", ".join(missing))

    def _access_token(self) -> str:
        try:
            import msal
        except ImportError as exc:
            raise ConfigurationError("SharePoint 사용을 위해 msal 패키지를 설치하세요.") from exc

        cache = msal.SerializableTokenCache()
        serialized = self.token_store.load()
        if serialized:
            cache.deserialize(serialized)
        authority = f"{self.settings.authority_host.rstrip('/')}/{self.settings.tenant_id}"
        app = msal.PublicClientApplication(
            self.settings.client_id,
            authority=authority,
            token_cache=cache,
        )
        result = None
        accounts = app.get_accounts()
        if accounts:
            result = app.acquire_token_silent(self.settings.scopes, account=accounts[0])
        if not result:
            flow = app.initiate_device_flow(scopes=self.settings.scopes)
            if "user_code" not in flow:
                raise AuthenticationError("Microsoft 로그인 절차를 시작할 수 없습니다.")
            self.login_notifier(flow.get("message", "Microsoft 로그인이 필요합니다."))
            result = app.acquire_token_by_device_flow(flow)
        if cache.has_state_changed:
            self.token_store.save(cache.serialize())
        token = (result or {}).get("access_token")
        if not token:
            detail = (result or {}).get("error_description", "토큰을 받지 못했습니다.")
            raise AuthenticationError(f"Microsoft 로그인 실패: {detail}")
        return token

    def fetch_products(self) -> ProductBatch:
        token = self._access_token()
        base = self.settings.graph_base_url.rstrip("/")
        site = urllib.parse.quote(self.settings.site_id, safe=",:")
        list_id = urllib.parse.quote(self.settings.list_id, safe="")
        select = urllib.parse.quote(GRAPH_FIELDS, safe=",")
        url = (
            f"{base}/sites/{site}/lists/{list_id}/items"
            f"?$expand=fields($select={select})&$top=999"
        )
        rows: list[dict] = []
        page = 0
        while url:
            page += 1
            if page > 1000:
                raise SourceError("SharePoint 페이지 수가 안전 한도를 초과했습니다.")
            payload = self._get_json(url, token)
            values = payload.get("value")
            if not isinstance(values, list):
                raise SourceError("SharePoint 응답에 value 배열이 없습니다.")
            for item in values:
                fields = item.get("fields") if isinstance(item, dict) else None
                if isinstance(fields, dict):
                    rows.append(fields)
            next_url = payload.get("@odata.nextLink")
            url = str(next_url) if next_url else ""
        versions = {str(row.get("DataVersion", "")).strip() for row in rows if row.get("DataVersion")}
        schemas = {int(row.get("SchemaVersion", 0)) for row in rows if row.get("SchemaVersion") is not None}
        if len(versions) != 1 or len(schemas) != 1:
            raise SourceError("모든 SharePoint 상품은 동일한 DataVersion과 SchemaVersion이어야 합니다.")
        version = next(iter(versions), "")
        schema = next(iter(schemas), 0)
        return ProductBatch(
            products=tuple(map_product(row, version, schema) for row in rows),
            data_version=version,
            schema_version=schema,
        )

    def _get_json(self, url: str, token: str) -> dict:
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in {429, 503} or attempt == 2:
                    detail = exc.read().decode("utf-8", errors="replace")[:500]
                    raise SourceError(f"SharePoint HTTP {exc.code}: {detail}") from exc
                retry_after = int(exc.headers.get("Retry-After", 2 ** attempt))
                time.sleep(min(retry_after, 10))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt == 2:
                    break
                time.sleep(2 ** attempt)
        raise SourceError(f"SharePoint 연결 실패: {last_error}")

