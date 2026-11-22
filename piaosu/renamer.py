"""安全非破坏性发票归档重命名模块（只复制不改删源文件，生成完整 SHA256 归档对照表）。"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from piaosu.models import InvoiceRecord

_ILLEGAL_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def sanitize_filename_part(text: str, max_len: int = 32) -> str:
    cleaned = _ILLEGAL_CHARS_RE.sub("_", (text or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        return "未知"
    if cleaned.upper() in _RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned[:max_len].rstrip(" .") or "未知"


def build_archive_filename(
    record: InvoiceRecord,
    pattern: str = "{date}_{total}元_{seller}_{tail4}",
) -> str:
    date_part = sanitize_filename_part(record.issue_date or "未知日期", 16)
    total_part = f"{record.total_amount:.2f}" if record.total_amount is not None else "0.00"
    seller_part = sanitize_filename_part(record.seller_name or "未知销方", 28)
    num = record.invoice_number.strip()
    tail4 = sanitize_filename_part(num[-4:] if len(num) >= 4 else (num or record.file_sha256[:6]), 12)
    inv_num = sanitize_filename_part(num or record.file_sha256[:8], 24)
    inv_type = sanitize_filename_part(record.invoice_type or "发票", 16)

    stem = pattern.format(
        date=date_part,
        total=total_part,
        seller=seller_part,
        tail4=tail4,
        number=inv_num,
        type=inv_type,
    )
    stem = sanitize_filename_part(stem, max_len=120)
    ext = Path(record.file_name).suffix.lower() or ".pdf"
    return f"{stem}{ext}"


def _unique_target_path(output_dir: Path, filename: str) -> Path:
    candidate = output_dir / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    idx = 2
    while True:
        alt = output_dir / f"{stem}_{idx}{suffix}"
        if not alt.exists():
            return alt
        idx += 1


def archive_rename_invoices(
    records: list[InvoiceRecord],
    output_dir: Path,
    *,
    include_warnings: bool = True,
    pattern: str = "{date}_{total}元_{seller}_{tail4}",
) -> tuple[Path, list[dict[str, str]]]:
    """将有效发票按规范命名复制到归档目录（绝不删除或修改源文件），并写出 归档对照表.json。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    allowed = ("ok", "warning") if include_warnings else ("ok",)
    manifest: list[dict[str, str]] = []

    for rec in records:
        if rec.status not in allowed:
            continue
        src = Path(rec.file_path)
        if not src.is_file():
            continue
        target_name = build_archive_filename(rec, pattern=pattern)
        dest = _unique_target_path(output_dir, target_name)
        shutil.copy2(src, dest)
        manifest.append(
            {
                "source": str(src),
                "source_name": rec.file_name,
                "archived_name": dest.name,
                "invoice_number": rec.invoice_number,
                "total_amount": f"{rec.total_amount:.2f}" if rec.total_amount is not None else "",
                "sha256": rec.file_sha256,
            }
        )

    map_path = output_dir / "归档对照表.json"
    tmp_path = output_dir / f".归档对照表.{os.getpid()}.tmp.json"
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(manifest),
        "items": manifest,
    }
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, map_path)
    return map_path, manifest
