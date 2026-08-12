from __future__ import annotations

import csv
import io
import urllib.error
import urllib.parse
import urllib.request

from .. import __version__
from ..config import GoogleSheetsSettings
from ..errors import ConfigurationError, SourceError
from .base import ProductBatch, ProductSource
from .tabular import rows_to_batch


def google_sheet_csv_url(spreadsheet_url: str, configured_gid: str = "") -> str:
    raw = spreadsheet_url.strip()
    if not raw:
        raise ConfigurationError("Google Sheet 주소가 설정되지 않았습니다.")
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme != "https" or parsed.netloc.casefold() != "docs.google.com":
        raise ConfigurationError("docs.google.com의 HTTPS Google Sheet 주소를 입력하세요.")
    parts = [part for part in parsed.path.split("/") if part]
    try:
        marker = parts.index("d")
        spreadsheet_id = parts[marker + 1]
    except (ValueError, IndexError) as exc:
        raise ConfigurationError("Google Sheet 문서 ID를 주소에서 찾을 수 없습니다.") from exc
    if not spreadsheet_id:
        raise ConfigurationError("Google Sheet 문서 ID가 비어 있습니다.")

    query = urllib.parse.parse_qs(parsed.query)
    fragment = urllib.parse.parse_qs(parsed.fragment)
    gid = configured_gid.strip() or next(iter(query.get("gid", [])), "") or next(
        iter(fragment.get("gid", [])), "0"
    )
    if not gid.isdigit():
        raise ConfigurationError("Google Sheet gid는 숫자여야 합니다.")
    return (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?"
        + urllib.parse.urlencode({"format": "csv", "gid": gid})
    )


class GoogleSheetsProductSource(ProductSource):
    def __init__(self, settings: GoogleSheetsSettings):
        self.settings = settings
        self.export_url = google_sheet_csv_url(settings.spreadsheet_url, settings.gid)

    def fetch_products(self) -> ProductBatch:
        request = urllib.request.Request(
            self.export_url,
            headers={
                "Accept": "text/csv",
                "User-Agent": f"BeyondPack/{__version__}",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=max(1, self.settings.timeout_seconds)
            ) as response:
                declared = int(response.headers.get("Content-Length", "0") or 0)
                if declared > self.settings.max_download_bytes:
                    raise SourceError("Google Sheet 파일이 허용 크기를 초과했습니다.")
                payload = response.read(self.settings.max_download_bytes + 1)
        except SourceError:
            raise
        except urllib.error.HTTPError as exc:
            raise SourceError(f"Google Sheet HTTP {exc.code} 오류입니다.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SourceError(f"Google Sheet 연결 실패: {exc}") from exc
        if len(payload) > self.settings.max_download_bytes:
            raise SourceError("Google Sheet 파일이 허용 크기를 초과했습니다.")
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise SourceError("Google Sheet CSV가 UTF-8 형식이 아닙니다.") from exc
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if reader.fieldnames is None:
            raise SourceError("Google Sheet 첫 행에 열 제목이 없습니다.")
        return rows_to_batch(
            reader,
            source_name="Google Sheet",
            content_fingerprint=payload,
        )
