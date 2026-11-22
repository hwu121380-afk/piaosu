"""SQLite 跨月持久化发票查重台账（WAL 并发安全 + 跨月/批内双查重 + 回滚 + JSON 备份）。"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from piaosu.models import InvoiceRecord, ValidationIssue


def default_ledger_path() -> Path:
    return Path.home() / ".piaosu" / "ledger.db"


class InvoiceLedger:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else default_ledger_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS batches (
                    batch_id TEXT PRIMARY KEY,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    record_count INTEGER NOT NULL DEFAULT 0,
                    total_amount TEXT NOT NULL DEFAULT '0.00'
                );

                CREATE TABLE IF NOT EXISTS invoices (
                    dedup_key TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    invoice_number TEXT NOT NULL DEFAULT '',
                    invoice_code TEXT NOT NULL DEFAULT '',
                    issue_date TEXT NOT NULL DEFAULT '',
                    seller_name TEXT NOT NULL DEFAULT '',
                    buyer_name TEXT NOT NULL DEFAULT '',
                    total_amount TEXT NOT NULL DEFAULT '',
                    file_sha256 TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (batch_id) REFERENCES batches(batch_id)
                );

                CREATE INDEX IF NOT EXISTS idx_invoices_sha256 ON invoices(file_sha256);
                CREATE INDEX IF NOT EXISTS idx_invoices_batch ON invoices(batch_id);
                """
            )

    def lookup(self, dedup_key: str, file_sha256: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT i.dedup_key, i.batch_id, i.invoice_number, i.issue_date,
                       i.total_amount, i.file_name, i.created_at, b.note
                FROM invoices i
                LEFT JOIN batches b ON i.batch_id = b.batch_id
                WHERE i.dedup_key = ? OR i.file_sha256 = ?
                LIMIT 1
                """,
                (dedup_key, file_sha256),
            ).fetchone()
            return dict(row) if row is not None else None

    def mark_duplicates(self, records: list[InvoiceRecord]) -> list[InvoiceRecord]:
        """同时检测：1) 跨月历史台账重复；2) 当前批次内重复（支持幂等重跑）。"""
        seen_keys: dict[str, str] = {}
        seen_hashes: dict[str, str] = {}
        for rec in records:
            # 清理上一轮重复标记，保证人工编辑后重跑幂等
            rec.issues = [i for i in rec.issues if i.code not in ("duplicate_history", "duplicate_batch")]
            rec.duplicate_ref = ""
            if rec.status in ("duplicate_history", "duplicate_batch"):
                if any(i.severity == "error" for i in rec.issues):
                    rec.status = "error"
                elif rec.issues:
                    rec.status = "warning"
                else:
                    rec.status = "ok"

            # 1. 先查历史台账
            hist = self.lookup(rec.dedup_key, rec.file_sha256)
            if hist is not None:
                rec.status = "duplicate_history"
                note_part = f"（{hist['note']}）" if hist.get("note") else ""
                rec.duplicate_ref = f"历史批次 {hist['batch_id']}{note_part} 于 {hist['created_at'][:10]} 已入账"
                rec.issues.append(
                    ValidationIssue("duplicate_history", "error", f"跨月重复报销：{rec.duplicate_ref}")
                )
                continue

            # 2. 再查本批次内重复
            prev_file = seen_keys.get(rec.dedup_key) or seen_hashes.get(rec.file_sha256)
            if prev_file is not None:
                rec.status = "duplicate_batch"
                rec.duplicate_ref = f"与本批文件 {prev_file} 重复"
                rec.issues.append(
                    ValidationIssue("duplicate_batch", "error", f"批内重复发票：{rec.duplicate_ref}")
                )
                continue

            if rec.invoice_number:
                seen_keys[rec.dedup_key] = rec.file_name
            seen_hashes[rec.file_sha256] = rec.file_name
        return records

    def commit_batch(self, records: list[InvoiceRecord], note: str = "", batch_id: str | None = None) -> tuple[str, int]:
        """将当前批次中有效（非重复、非错误）的发票写入永久台账。"""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        bid = batch_id or datetime.now(timezone.utc).strftime("B%Y%m%d-%H%M%S")
        eligible = [r for r in records if r.status in ("ok", "warning")]
        if not eligible:
            return bid, 0

        total = sum((r.total_amount for r in eligible if r.total_amount is not None), start=Decimal("0.00"))
        inserted = 0
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO batches(batch_id, note, created_at, record_count, total_amount) VALUES (?, ?, ?, ?, ?)",
                (bid, note, now, len(eligible), f"{total:.2f}"),
            )
            for rec in eligible:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO invoices(
                        dedup_key, batch_id, invoice_number, invoice_code, issue_date,
                        seller_name, buyer_name, total_amount, file_sha256, file_name, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rec.dedup_key,
                        bid,
                        rec.invoice_number,
                        rec.invoice_code,
                        rec.issue_date,
                        rec.seller_name,
                        rec.buyer_name,
                        f"{rec.total_amount:.2f}" if rec.total_amount is not None else "",
                        rec.file_sha256,
                        rec.file_name,
                        now,
                    ),
                )
                inserted += cur.rowcount
        return bid, inserted

    def list_batches(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT batch_id, note, created_at, record_count, total_amount FROM batches ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def total_invoice_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM invoices").fetchone()
            return int(row["cnt"]) if row is not None else 0

    def rollback_batch(self, batch_id: str) -> int:
        """撤销指定批次的入账记录，返回删除的发票条数。"""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM invoices WHERE batch_id = ?", (batch_id,))
            deleted = cur.rowcount
            conn.execute("DELETE FROM batches WHERE batch_id = ?", (batch_id,))
            return deleted

    def export_json(self, dest_path: Path) -> Path:
        """原子写入导出全部批次与发票记录，用于财务归档与跨设备备份。"""
        with self._connect() as conn:
            batches = [
                dict(r)
                for r in conn.execute(
                    "SELECT batch_id, note, created_at, record_count, total_amount FROM batches ORDER BY created_at DESC"
                ).fetchall()
            ]
            invoices = [
                dict(r)
                for r in conn.execute(
                    "SELECT dedup_key, batch_id, invoice_number, invoice_code, issue_date, "
                    "seller_name, buyer_name, total_amount, file_sha256, file_name, created_at "
                    "FROM invoices ORDER BY created_at DESC"
                ).fetchall()
            ]
        payload = {
            "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "batch_count": len(batches),
            "invoice_count": len(invoices),
            "batches": batches,
            "invoices": invoices,
        }
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = dest_path.with_name(f".{dest_path.name}.{os.getpid()}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, dest_path)
        return dest_path
