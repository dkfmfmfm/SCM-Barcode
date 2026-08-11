from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from .errors import PackagingValidationError
from .models import BoxGroupInput, BoxItem, utc_now_iso


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

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.row_factory = sqlite3.Row
        return conn

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
                    status TEXT NOT NULL
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

    def create_job(self, operator_name: str, product_db_version: str, app_version: str) -> str:
        now = utc_now_iso()
        job_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO packaging_jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, now, now, operator_name.strip(), product_db_version, app_version, "OPEN"),
            )
            self._audit(conn, operator_name, "CREATE", "JOB", job_id)
        return job_id

    def save_box_group(
        self, job_id: str, value: BoxGroupInput, operator_name: str
    ) -> SavedBoxGroup:
        if not value.items:
            raise PackagingValidationError("박스에 상품을 한 개 이상 추가하세요.")
        group_id = uuid.uuid4().hex
        now = utc_now_iso()
        with self._connect() as conn:
            job = conn.execute(
                "SELECT status FROM packaging_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if job is None or job["status"] != "OPEN":
                raise PackagingValidationError("저장 가능한 작업이 아닙니다.")
            start = int(
                conn.execute(
                    "SELECT COALESCE(MAX(box_start_no + box_count), 1) FROM box_groups WHERE job_id = ?",
                    (job_id,),
                ).fetchone()[0]
            )
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
                SELECT j.job_id, j.created_at, j.operator_name, j.product_db_version,
                       j.app_version, j.status, g.box_group_id, g.box_start_no,
                       g.box_count, g.weight_kg, g.length_cm, g.width_cm, g.height_cm,
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
                "SELECT * FROM box_groups WHERE job_id = ? ORDER BY box_start_no DESC LIMIT 1",
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

