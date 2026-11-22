"""PDF 电子发票双通道解析器：坐标排序矢量文本直提（PyMuPDF / pypdf）+ 扫描件本地 OCR 降级。"""

from __future__ import annotations

from pathlib import Path

from piaosu.models import InvoiceRecord, ValidationIssue
from piaosu.parsers.ocr_fallback import is_ocr_available, ocr_image_bytes
from piaosu.parsers.rules import extract_from_text

MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MiB


def _extract_vector_text(path: Path) -> tuple[str, str | None]:
    """返回 (提取的矢量文本, 致命错误说明或 None)。"""
    try:
        import pymupdf

        pymupdf.TOOLS.mupdf_display_errors(False)
        with pymupdf.open(str(path)) as doc:
            if doc.is_encrypted:
                return "", "该 PDF 已加密保护，无法直接解析。请先解密后再导入。"
            chunks: list[str] = []
            # 发票核心信息通常在首页，最多提取前 3 页避免超长销货清单干扰
            for idx in range(min(len(doc), 3)):
                page = doc[idx]
                text = page.get_text("text", sort=True)
                if text:
                    chunks.append(text)
            joined = "\n".join(chunks).strip()
            if joined:
                return joined, None
    except Exception:
        pass

    try:
        import logging
        from pypdf import PdfReader

        logging.getLogger("pypdf").setLevel(logging.ERROR)
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            return "", "该 PDF 已加密保护，无法直接解析。请先解密后再导入。"
        chunks = [(reader.pages[i].extract_text() or "") for i in range(min(len(reader.pages), 3))]
        return "\n".join(chunks).strip(), None
    except Exception as exc:
        return "", f"PDF 文件损坏或无法读取：{exc}"


def _render_first_page_png(path: Path) -> bytes | None:
    try:
        import pymupdf

        pymupdf.TOOLS.mupdf_display_errors(False)
        with pymupdf.open(str(path)) as doc:
            if len(doc) == 0 or doc.is_encrypted:
                return None
            pix = doc[0].get_pixmap(dpi=200)
            return pix.tobytes("png")
    except Exception:
        return None


def parse_pdf_file(path: Path, record: InvoiceRecord) -> InvoiceRecord:
    if path.stat().st_size > MAX_PDF_BYTES:
        record.parse_channel = "failed"
        record.status = "error"
        record.issues.append(ValidationIssue("file_too_large", "error", "PDF 体积超过 50 MiB 上限，已跳过。"))
        return record

    text, fatal_err = _extract_vector_text(path)
    if fatal_err:
        record.parse_channel = "failed"
        record.status = "error"
        record.issues.append(ValidationIssue("pdf_unreadable", "error", fatal_err))
        return record

    if len(text) >= 20:
        record.parse_channel = "pdf_vector"
        record.confidence = 0.99
        return extract_from_text(text, record)

    # 扫描件 PDF：降级到本地离线 OCR
    png_bytes = _render_first_page_png(path)
    if png_bytes and is_ocr_available():
        ocr_text, conf = ocr_image_bytes(png_bytes)
        record.parse_channel = "ocr"
        record.confidence = conf
        return extract_from_text(ocr_text, record)

    record.parse_channel = "failed"
    record.confidence = 0.0
    record.status = "error"
    record.issues.append(
        ValidationIssue(
            code="scanned_pdf_no_ocr",
            severity="error",
            message="该 PDF 未包含可提取矢量文本（疑似扫描件），请安装 rapidocr-onnxruntime 启用本地离线 OCR。",
        )
    )
    return record
