from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

from .errors import PackagingValidationError
from .models import BoxGroupInput, BoxItem, utc_now_iso
from .normalization import normalize_shipment_code


@dataclass(frozen=True, slots=True)
class SavedBoxGroup:
    job_id: str
    box_group_id: str
    box_start_no: int
    box_end_no: int


class PackagingRepository:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS packaging_jobs (
                    job_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    operator_name TEXT NOT NULL,
                    product_db_version TEXT NOT NULL,
                    app_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    shipment_code TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS box_groups (
                    box_group_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES packaging_jobs(job_id),
                    box_start_no INTEGER NOT NULL,
                    box_count INTEGER NOT NULL CHECK(box_count > 0),
                    weight_kg TEXT NOT NULL,
                    length_cm TEXT NOT NULL,
                    width_cm TEXT NOT NULL,
                    height_cm TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS box_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    box_group_id TEXT NOT NULL REFERENCES box_groups(box_group_id) ON DELETE CASCADE,
                    fnsku TEXT NOT NULL,
                    item_code TEXT NOT NULL,
                    sku TEXT NOT NULL,
                    country_code TEXT NOT NULL,
                    country_name TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    qty_per_box INTEGER NOT NULL CHECK(qty_per_box > 0),
                    source_modified_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS drafts (
                    draft_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    operator_name TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    details_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_box_groups_job ON box_groups(job_id);
                CREATE INDEX IF NOT EXISTS idx_box_items_fnsku ON box_items(fnsku);
                """
            )
            # 2.2.4 이전 DB에는 출고건 열이 없다. 기존 포장기록은 그대로 두고
            # 열만 추가해 앞으로의 박스번호를 출고건 단위로 잇는다.
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(packaging_jobs)").fetchall()
            }
            if "shipment_code" not in columns:
                conn.execute(
                    "ALTER TABLE packaging_jobs ADD COLUMN shipment_code TEXT NOT NULL DEFAULT ''"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_shipment ON packaging_jobs(shipment_code)"
            )

    def create_job(
        self,
        operator_name: str,
        product_db_version: str,
        app_version: str,
        shipment_code: str = "",
    ) -> str:
        now = utc_now_iso()
        job_id = uuid.uuid4().hex
        code = normalize_shipment_code(shipment_code)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO packaging_jobs(
                    job_id, created_at, updated_at, operator_name,
                    product_db_version, app_version, status, shipment_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    now,
                    now,
                    operator_name.strip(),
                    product_db_version,
                    app_version,
                    "OPEN",
                    code,
                ),
            )
            self._audit(
                conn,
                operator_name,
                "CREATE",
                "JOB",
                job_id,
                details={"shipment_code": code},
            )
        return job_id

    @staticmethod
    def _next_box_number(conn: sqlite3.Connection, shipment_code: str) -> int:
        """출고건 안에서 다음에 붙일 박스번호.

        박스번호는 작업(job)이 아니라 출고건 단위로 이어진다. 프로그램을 껐다
        켜도 같은 출고건이면 이어서 매기고, 다른 출고건이면 1부터 시작한다.
        """
        return int(
            conn.execute(
                """
                SELECT COALESCE(MAX(g.box_start_no + g.box_count), 1)
                FROM box_groups g
                JOIN packaging_jobs j ON j.job_id = g.job_id
                WHERE j.shipment_code = ?
                """,
                (shipment_code,),
            ).fetchone()[0]
        )

    def next_box_number(self, shipment_code: str) -> int:
        with self._connect() as conn:
            return self._next_box_number(conn, normalize_shipment_code(shipment_code))

    def recent_shipments(self, limit: int = 30) -> list[str]:
        """박스를 확정한 적이 있는 출고건을 최근 순으로 돌려준다.

        문서번호를 다시 타이핑하다 한 글자만 달라져도 박스번호가 1부터 다시
        시작하므로, 작업자가 목록에서 고를 수 있게 한다.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT j.shipment_code, MAX(g.created_at) AS last_at
                FROM packaging_jobs j
                JOIN box_groups g ON g.job_id = j.job_id
                WHERE j.shipment_code <> ''
                GROUP BY j.shipment_code
                ORDER BY last_at DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [str(row["shipment_code"]) for row in rows]

    def shipment_groups(self, shipment_code: str) -> list[dict]:
        """출고건에서 확정된 박스 묶음을 박스번호 순으로 돌려준다."""
        code = normalize_shipment_code(shipment_code)
        if not code:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT g.box_group_id, g.box_start_no, g.box_count, g.weight_kg,
                       g.length_cm, g.width_cm, g.height_cm, g.created_at,
                       j.operator_name,
                       COUNT(i.id) AS item_count,
                       COALESCE(SUM(i.qty_per_box), 0) AS total_qty,
                       MIN(i.item_code) AS first_item_code,
                       MIN(i.product_name) AS first_product_name
                FROM box_groups g
                JOIN packaging_jobs j ON j.job_id = g.job_id
                LEFT JOIN box_items i ON i.box_group_id = g.box_group_id
                WHERE j.shipment_code = ?
                GROUP BY g.box_group_id
                ORDER BY g.box_start_no
                """,
                (code,),
            ).fetchall()
        return [dict(row) for row in rows]

    def shipment_rows(self, shipment_code: str) -> list[dict]:
        """출고건 전체를 구성품 단위로 펼쳐 돌려준다. Excel 내보내기용."""
        code = normalize_shipment_code(shipment_code)
        if not code:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT j.shipment_code, j.job_id, j.created_at, j.operator_name,
                       j.product_db_version, j.app_version, j.status,
                       g.box_group_id, g.box_start_no, g.box_count,
                       g.weight_kg, g.length_cm, g.width_cm, g.height_cm,
                       g.created_at AS box_created_at,
                       i.fnsku, i.item_code, i.sku, i.country_code, i.country_name,
                       i.product_name, i.qty_per_box, i.source_modified_at
                FROM packaging_jobs j
                JOIN box_groups g ON g.job_id = j.job_id
                JOIN box_items i ON i.box_group_id = g.box_group_id
                WHERE j.shipment_code = ?
                ORDER BY g.box_start_no, i.id
                """,
                (code,),
            ).fetchall()
        return [dict(row) for row in rows]

    def rows_between(self, start_at: str, end_at: str) -> list[dict]:
        """확정 시각이 구간 안에 드는 실적을 구성품 단위로 돌려준다.

        `created_at`은 UTC ISO 고정 형식이라 문자열 비교로 구간을 자를 수 있다.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT j.shipment_code, j.job_id, j.created_at, j.operator_name,
                       j.product_db_version, j.app_version, j.status,
                       g.box_group_id, g.box_start_no, g.box_count,
                       g.weight_kg, g.length_cm, g.width_cm, g.height_cm,
                       g.created_at AS box_created_at,
                       i.fnsku, i.item_code, i.sku, i.country_code, i.country_name,
                       i.product_name, i.qty_per_box, i.source_modified_at
                FROM box_groups g
                JOIN packaging_jobs j ON j.job_id = g.job_id
                JOIN box_items i ON i.box_group_id = g.box_group_id
                WHERE g.created_at >= ? AND g.created_at < ?
                ORDER BY g.created_at, g.box_start_no, i.id
                """,
                (start_at, end_at),
            ).fetchall()
        return [dict(row) for row in rows]

    def box_group(self, box_group_id: str) -> tuple[dict, list[dict]] | None:
        """박스 묶음 하나와 구성품을 돌려준다. 지난 박스 재출력에 쓴다."""
        with self._connect() as conn:
            group = conn.execute(
                """
                SELECT g.*, j.shipment_code
                FROM box_groups g
                JOIN packaging_jobs j ON j.job_id = g.job_id
                WHERE g.box_group_id = ?
                """,
                (box_group_id,),
            ).fetchone()
            if not group:
                return None
            items = conn.execute(
                "SELECT * FROM box_items WHERE box_group_id = ? ORDER BY id",
                (box_group_id,),
            ).fetchall()
        return dict(group), [dict(row) for row in items]

    def save_box_group(
        self, job_id: str, value: BoxGroupInput, operator_name: str
    ) -> SavedBoxGroup:
        if not value.items:
            raise PackagingValidationError("박스에 상품을 한 개 이상 추가하세요.")
        group_id = uuid.uuid4().hex
        now = utc_now_iso()
        with self._connect() as conn:
            job = conn.execute(
                "SELECT status, shipment_code FROM packaging_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if job is None or job["status"] != "OPEN":
                raise PackagingValidationError("저장 가능한 작업이 아닙니다.")
            start = self._next_box_number(conn, str(job["shipment_code"]))
            conn.execute(
                """
                INSERT INTO box_groups VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    group_id,
                    job_id,
                    start,
                    value.box_count,
                    str(value.weight_kg),
                    str(value.length_cm),
                    str(value.width_cm),
                    str(value.height_cm),
                    now,
                ),
            )
            conn.executemany(
                """
                INSERT INTO box_items(
                    box_group_id, fnsku, item_code, sku, country_code,
                    country_name, product_name, qty_per_box, source_modified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        group_id,
                        item.fnsku,
                        item.item_code,
                        item.sku,
                        item.country_code,
                        item.country_name,
                        item.product_name,
                        item.qty_per_box,
                        item.source_modified_at,
                    )
                    for item in value.items
                ],
            )
            conn.execute(
                "UPDATE packaging_jobs SET updated_at = ? WHERE job_id = ?", (now, job_id)
            )
            self._audit(conn, operator_name, "CREATE", "BOX_GROUP", group_id, details=asdict(value))
        return SavedBoxGroup(job_id, group_id, start, start + value.box_count - 1)

    def close_job(self, job_id: str, operator_name: str) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE packaging_jobs SET status = 'COMPLETED', updated_at = ? WHERE job_id = ? AND status = 'OPEN'",
                (now, job_id),
            ).rowcount
            if not changed:
                raise PackagingValidationError("완료할 작업이 없습니다.")
            self._audit(conn, operator_name, "COMPLETE", "JOB", job_id)

    def save_draft(self, key: str, payload: dict) -> None:
        encoded = json.dumps(payload, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO drafts VALUES (?, ?, ?)
                ON CONFLICT(draft_key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (key, encoded, utc_now_iso()),
            )

    def load_draft(self, key: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM drafts WHERE draft_key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def clear_draft(self, key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM drafts WHERE draft_key = ?", (key,))

    def job_rows(self, job_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT j.job_id, j.shipment_code, j.created_at, j.operator_name,
                       j.product_db_version, j.app_version, j.status,
                       g.box_group_id, g.box_start_no,
                       g.box_count, g.weight_kg, g.length_cm, g.width_cm, g.height_cm,
                       g.created_at AS box_created_at,
                       i.fnsku, i.item_code, i.sku, i.country_code, i.country_name,
                       i.product_name, i.qty_per_box, i.source_modified_at
                FROM packaging_jobs j
                JOIN box_groups g ON g.job_id = j.job_id
                JOIN box_items i ON i.box_group_id = g.box_group_id
                WHERE j.job_id = ?
                ORDER BY g.box_start_no, i.id
                """,
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def last_group(self, job_id: str) -> tuple[dict, list[dict]] | None:
        with self._connect() as conn:
            group = conn.execute(
                """
                SELECT g.*, j.shipment_code
                FROM box_groups g
                JOIN packaging_jobs j ON j.job_id = g.job_id
                WHERE g.job_id = ?
                ORDER BY g.box_start_no DESC LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            if not group:
                return None
            items = conn.execute(
                "SELECT * FROM box_items WHERE box_group_id = ? ORDER BY id", (group["box_group_id"],)
            ).fetchall()
        return dict(group), [dict(row) for row in items]

    @staticmethod
    def _audit(
        conn: sqlite3.Connection,
        operator: str,
        action: str,
        entity_type: str,
        entity_id: str,
        reason: str = "",
        details: dict | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO audit_events(
                occurred_at, operator_name, action, entity_type, entity_id, reason, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now_iso(),
                operator,
                action,
                entity_type,
                entity_id,
                reason,
                json.dumps(details or {}, ensure_ascii=False, default=str),
            ),
        )
