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
    # 정정으로 번호가 밀린 뒤 박스들. 이미 붙여 둔 라벨과 달라지므로 화면이
    # 재부착 대상을 집어 주는 데 쓴다. 새 박스 확정에서는 항상 비어 있다.
    renumbered: tuple[dict, ...] = ()


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
            group_columns = {row["name"] for row in conn.execute("PRAGMA table_info(box_groups)")}
            for name in ("product_db_version", "verified_at", "operator_name"):
                if name not in group_columns:
                    conn.execute(f"ALTER TABLE box_groups ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS box_revisions (
                    box_group_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES packaging_jobs(job_id),
                    state TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    operator_name TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    replacement_id TEXT NOT NULL DEFAULT '',
                    snapshot_json TEXT NOT NULL
                )
            """)

    def job(self, job_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM packaging_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def search_jobs(
        self, search: str = "", status: str = "", start_at: str = "", end_at: str = ""
    ) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT j.*, COUNT(g.box_group_id) AS group_count,
                       COALESCE(SUM(g.box_count), 0) AS box_count
                FROM packaging_jobs j LEFT JOIN box_groups g ON g.job_id = j.job_id
                WHERE (? = '' OR instr(lower(j.shipment_code || ' ' || j.operator_name || ' ' || j.job_id), lower(?)) > 0)
                  AND (? = '' OR j.status = ?)
                  AND (? = '' OR j.updated_at >= ?)
                  AND (? = '' OR j.updated_at < ?)
                GROUP BY j.job_id ORDER BY j.updated_at DESC, j.job_id
                """, (search, search, status, status, start_at, start_at, end_at, end_at)).fetchall()
        return [dict(row) for row in rows]

    def resume_job(self, job_id: str, operator_name: str) -> dict:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM packaging_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row or row["status"] != "OPEN":
                raise PackagingValidationError("진행 중인 작업만 이어서 할 수 있습니다.")
            if not operator_name.strip():
                raise PackagingValidationError("작업자 이름 또는 사번을 입력하세요.")
            self._audit(conn, operator_name, "RESUME", "JOB", job_id)
            conn.execute("""INSERT INTO drafts VALUES ('active-job', ?, ?)
                ON CONFLICT(draft_key) DO UPDATE SET payload_json=excluded.payload_json,
                updated_at=excluded.updated_at""", (json.dumps({"job_id": job_id}), utc_now_iso()))
        return dict(row)

    def revision_history(self, job_id: str = "") -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("""SELECT r.*, j.shipment_code FROM box_revisions r
                JOIN packaging_jobs j ON j.job_id = r.job_id
                WHERE ? = '' OR r.job_id = ? ORDER BY r.occurred_at DESC, r.rowid DESC""",
                (job_id, job_id)).fetchall()
        return [dict(row) for row in rows]

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
                       COALESCE(NULLIF(g.operator_name, ''), j.operator_name) AS operator_name,
                       COUNT(i.id) AS item_count,
                       COALESCE(SUM(i.qty_per_box), 0) AS total_qty,
                       MIN(i.fnsku) AS first_fnsku,
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
                SELECT j.shipment_code, j.job_id, j.created_at,
                       COALESCE(NULLIF(g.operator_name, ''), j.operator_name) AS operator_name,
                       COALESCE(NULLIF(g.product_db_version, ''), j.product_db_version) AS product_db_version,
                       j.app_version, j.status,
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

    def is_last_box_group(self, box_group_id: str) -> bool:
        """이 박스 묶음이 해당 출고건의 마지막인지 확인한다."""
        with self._connect() as conn:
            return self._last_box_group_id(conn, box_group_id) == box_group_id

    @staticmethod
    def _last_box_group_id(conn: sqlite3.Connection, box_group_id: str = "") -> str:
        """같은 출고건에서 박스번호가 가장 큰 묶음의 id.

        `box_group_id`를 주면 그 묶음이 속한 출고건에서 찾는다.
        """
        if box_group_id:
            shipment = conn.execute(
                """
                SELECT j.shipment_code
                FROM box_groups g
                JOIN packaging_jobs j ON j.job_id = g.job_id
                WHERE g.box_group_id = ?
                """,
                (box_group_id,),
            ).fetchone()
            if shipment is None:
                return ""
            code = str(shipment["shipment_code"])
        else:
            return ""
        row = conn.execute(
            """
            SELECT g.box_group_id
            FROM box_groups g
            JOIN packaging_jobs j ON j.job_id = g.job_id
            WHERE j.shipment_code = ?
            ORDER BY g.box_start_no DESC, g.created_at DESC
            LIMIT 1
            """,
            (code,),
        ).fetchone()
        return str(row["box_group_id"]) if row else ""

    def take_back_box_group(
        self, box_group_id: str, operator_name: str, reason: str, action: str = "DELETE"
    ) -> tuple[dict, list[dict], list[dict]]:
        """확정한 박스 묶음을 지운다. 지운 내용과 번호가 밀린 묶음을 돌려준다.

        마지막 묶음이면 그 번호부터 다시 발번된다. 중간 묶음이면 뒤따르는
        묶음의 번호를 당겨 1번부터 빈틈없이 이어지게 한다. 번호를 당긴 묶음은
        이미 붙여 둔 라벨과 달라지므로 **라벨을 다시 붙여야 한다.** 어떤
        박스가 대상인지 세 번째 반환값으로 돌려준다.

        번호에 구멍을 내지 않는 쪽을 택한 이유는 포장실적과 패킹리스트가
        1..N으로 이어져야 하기 때문이다. 대신 재부착 비용이 생기므로, 화면은
        확정 전에 몇 박스가 대상인지 먼저 보여 준다(`renumber_preview`).
        """
        cleaned = reason.strip()
        if not cleaned:
            raise PackagingValidationError("수정·삭제 사유를 입력하세요.")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            group = conn.execute(
                """
                SELECT g.*, j.shipment_code
                FROM box_groups g
                JOIN packaging_jobs j ON j.job_id = g.job_id
                WHERE g.box_group_id = ?
                """,
                (box_group_id,),
            ).fetchone()
            if group is None:
                raise PackagingValidationError("해당 박스를 찾을 수 없습니다.")
            if not operator_name.strip():
                raise PackagingValidationError("작업자 이름 또는 사번을 입력하세요.")
            status = conn.execute("SELECT status FROM packaging_jobs WHERE job_id = ?", (group["job_id"],)).fetchone()[0]
            if status != "OPEN":
                raise PackagingValidationError("완료된 작업은 수정·취소할 수 없습니다.")
            items = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM box_items WHERE box_group_id = ? ORDER BY id",
                    (box_group_id,),
                ).fetchall()
            ]
            saved = dict(group)
            self._archive_group(conn, saved, items, "CANCELLED", operator_name, cleaned)
            conn.execute("DELETE FROM box_items WHERE box_group_id = ?", (box_group_id,))
            conn.execute("DELETE FROM box_groups WHERE box_group_id = ?", (box_group_id,))
            moved = self._shift_following(
                conn,
                str(saved["shipment_code"]),
                int(saved["box_start_no"]),
                -int(saved["box_count"]),
            )
            conn.execute(
                "UPDATE packaging_jobs SET updated_at = ? WHERE job_id = ?",
                (utc_now_iso(), saved["job_id"]),
            )
            self._audit(
                conn,
                operator_name,
                action,
                "BOX_GROUP",
                box_group_id,
                reason=cleaned,
                details={
                    "group": saved,
                    "items": items,
                    "renumbered": moved,
                },
            )
        return saved, items, moved

    @staticmethod
    def _following_groups(
        conn: sqlite3.Connection, shipment_code: str, after_start_no: int
    ) -> list[dict]:
        """같은 출고건에서 이 번호보다 뒤에 오는 묶음을 순서대로 돌려준다."""
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT g.box_group_id, g.box_start_no, g.box_count
                FROM box_groups g
                JOIN packaging_jobs j ON j.job_id = g.job_id
                WHERE j.shipment_code = ? AND g.box_start_no > ?
                ORDER BY g.box_start_no
                """,
                (shipment_code, int(after_start_no)),
            ).fetchall()
        ]

    @staticmethod
    def _moved(row: dict, delta: int) -> dict:
        old = int(row["box_start_no"])
        count = int(row["box_count"])
        return {
            "box_group_id": str(row["box_group_id"]),
            "box_count": count,
            "old_start_no": old,
            "old_end_no": old + count - 1,
            "new_start_no": old + delta,
            "new_end_no": old + delta + count - 1,
        }

    def _shift_following(
        self,
        conn: sqlite3.Connection,
        shipment_code: str,
        after_start_no: int,
        delta: int,
    ) -> list[dict]:
        """뒤따르는 묶음의 박스번호를 `delta`만큼 밀어 번호를 다시 이어 붙인다.

        번호가 밀린 묶음은 이미 붙여 둔 라벨과 달라진다. 어떤 묶음이 어디서
        어디로 갔는지 돌려주어, 화면이 재부착할 박스를 정확히 집어 줄 수 있게
        한다.
        """
        if not delta:
            return []
        moved = [
            self._moved(row, delta)
            for row in self._following_groups(conn, shipment_code, after_start_no)
        ]
        for entry in moved:
            conn.execute(
                "UPDATE box_groups SET box_start_no = ? WHERE box_group_id = ?",
                (entry["new_start_no"], entry["box_group_id"]),
            )
        return moved

    def renumber_preview(
        self, box_group_id: str, new_box_count: int | None = None
    ) -> list[dict]:
        """지우거나 수량을 바꾸면 번호가 밀릴 묶음을 미리 돌려준다. 쓰지 않는다.

        "이후 N박스의 라벨을 다시 붙여야 합니다"를 확정 **전에** 보여 주려면
        먼저 알아야 한다. `new_box_count`가 없으면 삭제로 간주한다.
        """
        with self._connect() as conn:
            group = conn.execute(
                """
                SELECT g.box_start_no, g.box_count, j.shipment_code
                FROM box_groups g
                JOIN packaging_jobs j ON j.job_id = g.job_id
                WHERE g.box_group_id = ?
                """,
                (box_group_id,),
            ).fetchone()
            if group is None:
                return []
            count = int(group["box_count"])
            delta = -count if new_box_count is None else int(new_box_count) - count
            if not delta:
                return []
            return [
                self._moved(row, delta)
                for row in self._following_groups(
                    conn, str(group["shipment_code"]), int(group["box_start_no"])
                )
            ]

    @staticmethod
    def _archive_group(conn, group, items, state, operator, reason, replacement_id=""):
        conn.execute("INSERT INTO box_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
            group["box_group_id"], group["job_id"], state, utc_now_iso(), operator,
            reason, replacement_id,
            json.dumps({"group": group, "items": items}, ensure_ascii=False, default=str),
        ))

    def audit_events(self, limit: int = 200) -> list[dict]:
        """감사 기록을 최근 순으로 돌려준다. 수정·삭제 이력 확인용."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT occurred_at, operator_name, action, entity_type,
                       entity_id, reason, details_json
                FROM audit_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def rows_between(self, start_at: str, end_at: str) -> list[dict]:
        """확정 시각이 구간 안에 드는 실적을 구성품 단위로 돌려준다.

        `created_at`은 UTC ISO 고정 형식이라 문자열 비교로 구간을 자를 수 있다.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT j.shipment_code, j.job_id, j.created_at,
                       COALESCE(NULLIF(g.operator_name, ''), j.operator_name) AS operator_name,
                       COALESCE(NULLIF(g.product_db_version, ''), j.product_db_version) AS product_db_version,
                       j.app_version, j.status,
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
        self, job_id: str, value: BoxGroupInput, operator_name: str,
        *, product_db_version: str = "", verified_at: str = "",
        draft_key: str | None = None, replaces_group_id: str = "", reason: str = ""
    ) -> SavedBoxGroup:
        if not value.items:
            raise PackagingValidationError("박스에 상품을 한 개 이상 추가하세요.")
        group_id = uuid.uuid4().hex
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job = conn.execute(
                "SELECT status, shipment_code FROM packaging_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if job is None or job["status"] != "OPEN":
                raise PackagingValidationError("저장 가능한 작업이 아닙니다.")
            renumbered: list[dict] = []
            if replaces_group_id:
                if not reason.strip():
                    raise PackagingValidationError("정정 사유를 입력하세요.")
                original = conn.execute("SELECT * FROM box_groups WHERE box_group_id = ? AND job_id = ?",
                    (replaces_group_id, job_id)).fetchone()
                if original is None:
                    raise PackagingValidationError("정정 대상이 변경됐습니다. 박스를 다시 선택하세요.")
                old_items = [dict(row) for row in conn.execute(
                    "SELECT * FROM box_items WHERE box_group_id = ? ORDER BY id", (replaces_group_id,))]
                self._archive_group(conn, dict(original), old_items, "CORRECTED", operator_name, reason.strip(), group_id)
                conn.execute("DELETE FROM box_items WHERE box_group_id = ?", (replaces_group_id,))
                conn.execute("DELETE FROM box_groups WHERE box_group_id = ?", (replaces_group_id,))
                # 정정본은 원본의 박스번호를 그대로 물려받는다. 새 번호를
                # 받으면 이미 붙여 둔 라벨과 어긋나기 때문이다. 수량이 달라진
                # 만큼만 뒤따르는 묶음을 밀어 1..N이 빈틈없이 이어지게 한다.
                start = int(original["box_start_no"])
                renumbered = self._shift_following(
                    conn, str(job["shipment_code"]), start,
                    value.box_count - int(original["box_count"]),
                )
            else:
                start = self._next_box_number(conn, str(job["shipment_code"]))
            conn.execute(
                """
                INSERT INTO box_groups(box_group_id, job_id, box_start_no, box_count,
                    weight_kg, length_cm, width_cm, height_cm, created_at,
                    product_db_version, verified_at, operator_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    product_db_version,
                    verified_at,
                    operator_name,
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
            if replaces_group_id:
                self._audit(conn, operator_name, "CORRECT", "BOX_GROUP", replaces_group_id,
                    reason=reason.strip(), details={"replacement_id": group_id,
                        "value": asdict(value), "renumbered": renumbered})
            if draft_key:
                conn.execute("DELETE FROM drafts WHERE draft_key = ?", (draft_key,))
            conn.execute("""INSERT INTO drafts VALUES ('active-job', ?, ?)
                ON CONFLICT(draft_key) DO UPDATE SET payload_json=excluded.payload_json,
                updated_at=excluded.updated_at""", (json.dumps({"job_id": job_id}), now))
        return SavedBoxGroup(
            job_id, group_id, start, start + value.box_count - 1, tuple(renumbered)
        )

    def close_job(self, job_id: str, operator_name: str) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not operator_name.strip():
                raise PackagingValidationError("작업자 이름 또는 사번을 입력하세요.")
            for draft in conn.execute("SELECT payload_json FROM drafts WHERE draft_key <> 'active-job'"):
                if json.loads(draft[0]).get("job_id") == job_id:
                    raise PackagingValidationError("미완료 입력을 확정하거나 초기화한 뒤 완료하세요.")
            changed = conn.execute(
                "UPDATE packaging_jobs SET status = 'COMPLETED', updated_at = ? WHERE job_id = ? AND status = 'OPEN'",
                (now, job_id),
            ).rowcount
            if not changed:
                raise PackagingValidationError("완료할 작업이 없습니다.")
            self._audit(conn, operator_name, "COMPLETE", "JOB", job_id)
            active = conn.execute("SELECT payload_json FROM drafts WHERE draft_key = 'active-job'").fetchone()
            if active and json.loads(active[0]).get("job_id") == job_id:
                conn.execute("DELETE FROM drafts WHERE draft_key = 'active-job'")

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
                SELECT j.job_id, j.shipment_code, j.created_at,
                       COALESCE(NULLIF(g.operator_name, ''), j.operator_name) AS operator_name,
                       COALESCE(NULLIF(g.product_db_version, ''), j.product_db_version) AS product_db_version,
                       j.app_version, j.status,
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
