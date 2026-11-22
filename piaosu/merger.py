"""A4 二合一电子发票矢量合并打印引擎（保留原矢量清晰度与电子印章，附居中裁剪虚线）。"""

from __future__ import annotations

from pathlib import Path

from piaosu.models import InvoiceRecord

A4_WIDTH = 595.28   # pt (210mm)
A4_HEIGHT = 841.89  # pt (297mm)


def merge_pdfs_2up(
    pdf_paths: list[Path],
    output_pdf: Path,
    *,
    margin_pt: float = 20.0,
    draw_cut_line: bool = True,
) -> tuple[Path, int]:
    """将多张 PDF 发票按每页 2 张（上下排版）无损矢量合并到 A4 打印 PDF。

    返回 (输出路径, 实际合并的原始页面数)。
    """
    import pymupdf

    if not pdf_paths:
        raise ValueError("没有可供合并的 PDF 发票文件。")

    out_doc = pymupdf.open()
    source_pages: list[tuple[Path, int]] = []
    for path in pdf_paths:
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        with pymupdf.open(str(path)) as src:
            for idx in range(len(src)):
                source_pages.append((path, idx))

    if not source_pages:
        out_doc.close()
        raise ValueError("未找到有效的 PDF 页面。")

    half_h = A4_HEIGHT / 2.0
    slots = [
        pymupdf.Rect(margin_pt, margin_pt, A4_WIDTH - margin_pt, half_h - margin_pt / 2.0),
        pymupdf.Rect(margin_pt, half_h + margin_pt / 2.0, A4_WIDTH - margin_pt, A4_HEIGHT - margin_pt),
    ]

    for i in range(0, len(source_pages), 2):
        page = out_doc.new_page(width=A4_WIDTH, height=A4_HEIGHT)
        pair = source_pages[i : i + 2]
        for slot_idx, (src_path, page_no) in enumerate(pair):
            with pymupdf.open(str(src_path)) as src_doc:
                page.show_pdf_page(slots[slot_idx], src_doc, page_no, keep_proportion=True)
        if draw_cut_line:
            y = half_h
            page.draw_line(
                pymupdf.Point(margin_pt, y),
                pymupdf.Point(A4_WIDTH - margin_pt, y),
                color=(0.6, 0.6, 0.6),
                width=0.6,
                dashes="[4 4] 0",
            )

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    out_doc.save(str(output_pdf), garbage=3, deflate=True)
    out_doc.close()
    return output_pdf, len(source_pages)


def merge_valid_record_pdfs(
    records: list[InvoiceRecord],
    output_pdf: Path,
    include_warnings: bool = True,
) -> tuple[Path, int]:
    allowed = ("ok", "warning") if include_warnings else ("ok",)
    pdfs = [
        Path(r.file_path)
        for r in records
        if r.status in allowed and Path(r.file_path).suffix.lower() == ".pdf"
    ]
    return merge_pdfs_2up(pdfs, output_pdf)
