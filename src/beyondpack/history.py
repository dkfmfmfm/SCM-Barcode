from __future__ import annotations

import json
from datetime import datetime, time, timezone
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QDialog, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from .exporter import export_job_xlsx


def local_stamp(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


class JobHistoryDialog(QDialog):
    """Search saved jobs independently of the current shipment/session."""

    def __init__(self, repository, parent):
        super().__init__(parent)
        self.repository = repository
        self.selected_job_id = ""
        self.jobs = []
        self.groups = []
        self.revisions = []
        self.setWindowTitle("이전 작업 조회·이어하기")
        self.resize(1080, 700)
        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("출고건·작업자·작업ID 검색")
        self.status = QComboBox()
        for label, value in (("전체 상태", ""), ("진행 중", "OPEN"), ("완료", "COMPLETED")):
            self.status.addItem(label, value)
        self.start = QDateEdit(QDate(2000, 1, 1))
        self.end = QDateEdit(QDate.currentDate())
        for widget in (self.start, self.end):
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("yyyy-MM-dd")
        find = QPushButton("조회")
        find.clicked.connect(self.refresh)
        self.search.returnPressed.connect(self.refresh)
        for widget in (self.search, self.status, QLabel("최근 작업일"), self.start, QLabel("~"), self.end, find):
            filters.addWidget(widget)
        layout.addLayout(filters)
        self.table = self._table(["출고건", "최근 작업", "작업자", "상태", "확정 박스", "작업ID"])
        self.table.itemSelectionChanged.connect(self.show_job)
        layout.addWidget(self.table, 2)
        self.tabs = QTabWidget()
        self.group_table = self._table(["박스번호", "박스수량", "무게(kg)", "확정시각"])
        self.revision_table = self._table(["처리", "원래 박스번호", "사유", "처리자", "시각"])
        self.revision_table.cellDoubleClicked.connect(self.show_revision)
        self.tabs.addTab(self.group_table, "확정 박스")
        self.tabs.addTab(self.revision_table, "취소·정정 이력 (더블클릭: 원본)")
        layout.addWidget(self.tabs, 2)
        actions = QHBoxLayout()
        self.resume = QPushButton("선택 작업 이어하기")
        self.resume.clicked.connect(self.choose_job)
        export = QPushButton("선택 작업 Excel 저장")
        export.clicked.connect(self.export_job)
        reprint = QPushButton("선택 박스 라벨 재출력")
        reprint.clicked.connect(self.print_group)
        close = QPushButton("닫기")
        close.clicked.connect(self.reject)
        for widget in (self.resume, export, reprint, close):
            actions.addWidget(widget)
        layout.addLayout(actions)
        layout.addWidget(QLabel("완료된 작업은 조회·출력만 가능합니다. 취소·정정 원본은 실적 합계에 포함되지 않습니다."))
        self.refresh()

    @staticmethod
    def _table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    @staticmethod
    def fill(table, rows):
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, value in enumerate(row):
                table.setItem(i, j, QTableWidgetItem(str(value)))

    def refresh(self):
        def bound(qdate):
            return datetime.combine(qdate.toPython(), time.min).astimezone().astimezone(timezone.utc).isoformat()
        self.jobs = self.repository.search_jobs(self.search.text().strip(), str(self.status.currentData()),
            bound(self.start.date()), bound(self.end.date().addDays(1)))
        self.fill(self.table, [(j["shipment_code"] or "(구버전)", local_stamp(j["updated_at"]),
            j["operator_name"], "진행 중" if j["status"] == "OPEN" else "완료", j["box_count"], j["job_id"]) for j in self.jobs])
        self.show_job()

    def current_job(self):
        row = self.table.currentRow()
        return self.jobs[row] if 0 <= row < len(self.jobs) else None

    def show_job(self):
        job = self.current_job()
        self.resume.setEnabled(bool(job and job["status"] == "OPEN" and job["shipment_code"]))
        rows = self.repository.job_rows(job["job_id"]) if job else []
        self.groups = list({row["box_group_id"]: row for row in rows}.values())
        self.fill(self.group_table, [(f"#{g['box_start_no']}~#{g['box_start_no'] + g['box_count'] - 1}",
            g["box_count"], g["weight_kg"], local_stamp(g["box_created_at"])) for g in self.groups])
        self.revisions = self.repository.revision_history(job["job_id"]) if job else []
        self.fill(self.revision_table, [("취소" if r["state"] == "CANCELLED" else "정정",
            json.loads(r["snapshot_json"])["group"]["box_start_no"], r["reason"],
            r["operator_name"], local_stamp(r["occurred_at"])) for r in self.revisions])

    def choose_job(self):
        job = self.current_job()
        if job and job["status"] == "OPEN" and job["shipment_code"]:
            self.selected_job_id = job["job_id"]
            self.accept()

    def export_job(self):
        job = self.current_job()
        if not job:
            return
        filename, _ = QFileDialog.getSaveFileName(self, "작업 Excel 저장",
            f"BeyondPack-{job['job_id'][:8]}.xlsx", "Excel (*.xlsx)")
        if filename:
            try:
                export_job_xlsx(self.repository, job["job_id"], Path(filename))
            except Exception as exc:
                QMessageBox.warning(self, "저장 실패", str(exc))

    def print_group(self):
        row = self.group_table.currentRow()
        if 0 <= row < len(self.groups):
            saved = self.repository.box_group(self.groups[row]["box_group_id"])
            if saved:
                self.parent()._print_box_labels(*saved, ask=True)

    def show_revision(self, row, _column):
        if not 0 <= row < len(self.revisions):
            return
        revision = self.revisions[row]
        snapshot = json.loads(revision["snapshot_json"])
        group = snapshot["group"]
        text = (f"원래 박스 #{group['box_start_no']} / {group['box_count']}박스\n"
            f"무게 {group['weight_kg']}kg / {group['length_cm']} × {group['width_cm']} × {group['height_cm']}cm\n"
            f"사유: {revision['reason']}\n\n")
        text += "\n".join(f"{i['fnsku']} · {i['product_name']} · {i['qty_per_box']} EA/BOX" for i in snapshot["items"])
        QMessageBox.information(self, "취소·정정 전 원본", text)
