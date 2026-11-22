"""统一票据解析入口：根据扩展名自动分发到 XML / OFD / PDF / OCR 通道。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from piaosu.models import InvoiceRecord, ValidationIssue
from piaosu.parsers.ocr_fallback import ocr_image_path
from piaosu.parsers.ofd_parser import parse_ofd_file
from piaosu.parsers.pdf_parser import parse_pdf_file
from piaosu.parsers.rules import extract_from_text
from piaosu.parsers.xml_parser import parse_xml_file

SUPPORTED_EXTENSIONS = frozenset({".xml", ".ofd", ".pdf", ".jpg", ".jpeg", ".png", ".bmp"})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_invoice_file(path: Path) -> InvoiceRecord:
    resolved = path.resolve()
    digest = sha256_file(resolved)
    record = InvoiceRecord(
        file_path=str(resolved),
        file_name=resolved.name,
        file_sha256=digest,
        parse_channel="failed",
    )
    ext = resolved.suffix.lower()
    try:
        if ext == ".xml":
            return parse_xml_file(resolved, record)
        if ext == ".ofd":
            return parse_ofd_file(resolved, record)
        if ext == ".pdf":
            return parse_pdf_file(resolved, record)
        if ext in {".jpg", ".jpeg", ".png", ".bmp"}:
            text, conf = ocr_image_path(resolved)
            record.parse_channel = "ocr"
            record.confidence = conf
            return extract_from_text(text, record)
        record.status = "error"
        record.issues.append(ValidationIssue("unsupported_format", "error", f"不支持的文件格式：{ext}"))
        return record
    except Exception as exc:
        record.parse_channel = "failed"
        record.confidence = 0.0
        record.status = "error"
        record.issues.append(ValidationIssue("parse_exception", "error", f"解析失败：{exc}"))
        return record
