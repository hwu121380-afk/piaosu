"""并发批处理编排服务：文件发现 -> 多线程双通道解析 -> 财务校验 -> 跨月查重 -> 统计汇总。"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

from piaosu.ledger import InvoiceLedger
from piaosu.models import BatchSummary, InvoiceRecord
from piaosu.parsers import SUPPORTED_EXTENSIONS, parse_invoice_file
from piaosu.validator import validate_invoice


def collect_invoice_files(inputs: list[Path], recursive: bool = True) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for item in inputs:
        p = item.resolve()
        if p.is_file():
            if p.suffix.lower() in SUPPORTED_EXTENSIONS and p not in seen:
                seen.add(p)
                found.append(p)
        elif p.is_dir():
            iterator = p.rglob("*") if recursive else p.glob("*")
            for child in sorted(iterator):
                if child.is_file() and child.suffix.lower() in SUPPORTED_EXTENSIONS:
                    rc = child.resolve()
                    if rc not in seen:
                        seen.add(rc)
                        found.append(rc)
    return found


def _parse_and_validate(path: Path) -> InvoiceRecord:
    return validate_invoice(parse_invoice_file(path))


def summarize_batch(records: list[InvoiceRecord], elapsed_ms: float) -> BatchSummary:
    summary = BatchSummary(total_files=len(records), elapsed_ms=round(elapsed_ms, 2))
    for rec in records:
        summary.channel_counts[rec.parse_channel] = summary.channel_counts.get(rec.parse_channel, 0) + 1
        if rec.status == "ok":
            summary.parsed_ok += 1
        elif rec.status == "warning":
            summary.warning_count += 1
        elif rec.status == "duplicate_batch":
            summary.duplicate_batch_count += 1
        elif rec.status == "duplicate_history":
            summary.duplicate_history_count += 1
        else:
            summary.error_count += 1

        if rec.status in ("ok", "warning"):
            if rec.total_amount is not None:
                summary.valid_total_amount += rec.total_amount
            if rec.tax_amount is not None:
                summary.valid_tax_amount += rec.tax_amount
    return summary


def process_invoices(
    inputs: list[Path],
    *,
    ledger: InvoiceLedger | None = None,
    recursive: bool = True,
    max_workers: int = 4,
) -> tuple[list[InvoiceRecord], BatchSummary]:
    t0 = time.perf_counter()
    files = collect_invoice_files(inputs, recursive=recursive)
    if not files:
        return [], BatchSummary(elapsed_ms=round((time.perf_counter() - t0) * 1000, 2))

    if max_workers <= 1 or len(files) == 1:
        records = [_parse_and_validate(f) for f in files]
    else:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(files))) as pool:
            records = list(pool.map(_parse_and_validate, files))

    active_ledger = ledger if ledger is not None else InvoiceLedger()
    active_ledger.mark_duplicates(records)

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return records, summarize_batch(records, elapsed_ms)
