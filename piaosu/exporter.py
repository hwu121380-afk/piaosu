"""Excel 报销台账导出器与企业自定义模板填充引擎（含公式注入防护与原子写入）。"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from piaosu.models import BatchSummary, InvoiceRecord

_STATUS_LABELS = {
    "ok": "正常",
    "warning": "待复核",
    "duplicate_batch": "批内重复",
    "duplicate_history": "历史重复",
    "error": "异常",
}

_CHANNEL_LABELS = {
    "xml": "数电XML原生",
    "ofd_xml": "OFD内嵌XML",
    "ofd_text": "OFD矢量文本",
    "pdf_vector": "PDF矢量文本",
    "ocr": "本地OCR",
    "failed": "解析失败",
}

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_HEADER_FILL = PatternFill("solid", fgColor="1B3044")
_HEADER_FONT = Font(name="Microsoft YaHei", size=11, bold=True, color="FFFFFF")
_BODY_FONT = Font(name="Microsoft YaHei", size=10, color="1D2430")
_ERR_FILL = PatternFill("solid", fgColor="FDECEA")
_WARN_FILL = PatternFill("solid", fgColor="FFF4E5")
_TOTAL_FILL = PatternFill("solid", fgColor="EDF2F7")
_THIN = Side(border_style="thin", color="CBD5E1")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def sanitize_excel_cell(value: Any) -> Any:
    """防止恶意票据名称注入 =CMD|... / +... / @... 等 Excel 公式（DDE/CSV 注入防护）。"""
    if isinstance(value, str):
        stripped = value.lstrip()
        if value and (value[0] in _FORMULA_PREFIXES or (stripped and stripped[0] in _FORMULA_PREFIXES)):
            return "'" + value
    return value


def _atomic_save_workbook(wb: Workbook, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp.xlsx")
    try:
        wb.save(str(tmp_path))
        os.replace(tmp_path, output_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
    return output_path


def _money(val: Decimal | None) -> float | str:
    return float(val) if val is not None else ""


def export_reimbursement_excel(
    records: list[InvoiceRecord],
    summary: BatchSummary,
    output_path: Path,
) -> Path:
    wb = Workbook()

    # Sheet 1: 报销明细
    ws = wb.active
    ws.title = "报销明细"
    headers = [
        "序号",
        "状态",
        "解析通道",
        "发票类型",
        "发票号码",
        "发票代码",
        "开票日期",
        "销售方名称",
        "销售方税号",
        "购买方名称",
        "购买方税号",
        "项目/品名摘要",
        "不含税金额",
        "税额",
        "价税合计",
        "税率",
        "备注/查重说明",
        "源文件名",
    ]
    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = _BORDER
    ws.row_dimensions[1].height = 26

    for idx, rec in enumerate(records, start=1):
        note_parts = []
        if rec.duplicate_ref:
            note_parts.append(rec.duplicate_ref)
        if rec.issues:
            note_parts.extend(i.message for i in rec.issues if i.code not in ("duplicate_batch", "duplicate_history"))
        if rec.remark:
            note_parts.append(rec.remark)

        row_values = [
            idx,
            _STATUS_LABELS.get(rec.status, rec.status),
            _CHANNEL_LABELS.get(rec.parse_channel, rec.parse_channel),
            sanitize_excel_cell(rec.invoice_type),
            sanitize_excel_cell(rec.invoice_number),
            sanitize_excel_cell(rec.invoice_code),
            sanitize_excel_cell(rec.issue_date),
            sanitize_excel_cell(rec.seller_name),
            sanitize_excel_cell(rec.seller_tax_id),
            sanitize_excel_cell(rec.buyer_name),
            sanitize_excel_cell(rec.buyer_tax_id),
            sanitize_excel_cell(rec.item_summary),
            _money(rec.amount_excl_tax),
            _money(rec.tax_amount),
            _money(rec.total_amount),
            sanitize_excel_cell(rec.tax_rate),
            sanitize_excel_cell("；".join(note_parts)),
            sanitize_excel_cell(rec.file_name),
        ]
        ws.append(row_values)
        row_idx = idx + 1
        row_fill = None
        if rec.status in ("error", "duplicate_batch", "duplicate_history"):
            row_fill = _ERR_FILL
        elif rec.status == "warning":
            row_fill = _WARN_FILL

        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.font = _BODY_FONT
            cell.border = _BORDER
            if row_fill is not None:
                cell.fill = row_fill
            if col_idx in (13, 14, 15) and isinstance(cell.value, float):
                cell.number_format = "#,##0.00"
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif col_idx in (1, 2, 3, 7, 16):
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")

    # 合计行（仅统计非重复、非错误的有效票据）
    total_row = len(records) + 2
    ws.cell(row=total_row, column=1, value="有效合计").font = Font(name="Microsoft YaHei", size=10, bold=True)
    ws.cell(row=total_row, column=2, value=f"有效 {summary.parsed_ok + summary.warning_count} 张")
    ws.cell(row=total_row, column=14, value=float(summary.valid_tax_amount)).number_format = "#,##0.00"
    ws.cell(row=total_row, column=15, value=float(summary.valid_total_amount)).number_format = "#,##0.00"
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=total_row, column=col_idx)
        cell.fill = _TOTAL_FILL
        cell.border = _BORDER
        if col_idx in (14, 15):
            cell.font = Font(name="Microsoft YaHei", size=10, bold=True)
            cell.alignment = Alignment(horizontal="right", vertical="center")

    widths = [7, 10, 13, 22, 22, 14, 12, 26, 20, 24, 20, 28, 12, 11, 12, 8, 36, 26]
    for col_idx, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = w
    ws.freeze_panes = "A2"

    # Sheet 2: 销方汇总
    ws_seller = wb.create_sheet("销方汇总")
    ws_seller.append(["销售方名称", "有效发票张数", "有效不含税金额", "有效税额", "有效价税合计"])
    for c in range(1, 6):
        cell = ws_seller.cell(row=1, column=c)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")

    grouped: dict[str, list[InvoiceRecord]] = defaultdict(list)
    for rec in records:
        if rec.status in ("ok", "warning"):
            grouped[rec.seller_name or "（未识别销方）"].append(rec)
    for seller, items in sorted(grouped.items(), key=lambda kv: kv[0]):
        excl_sum = sum((r.amount_excl_tax for r in items if r.amount_excl_tax is not None), Decimal("0.00"))
        tax_sum = sum((r.tax_amount for r in items if r.tax_amount is not None), Decimal("0.00"))
        tot_sum = sum((r.total_amount for r in items if r.total_amount is not None), Decimal("0.00"))
        ws_seller.append([sanitize_excel_cell(seller), len(items), float(excl_sum), float(tax_sum), float(tot_sum)])
    for r_idx in range(2, ws_seller.max_row + 1):
        for c_idx in (3, 4, 5):
            ws_seller.cell(row=r_idx, column=c_idx).number_format = "#,##0.00"
    for c_idx, w in enumerate([30, 14, 16, 14, 16], start=1):
        ws_seller.column_dimensions[get_column_letter(c_idx)].width = w

    # Sheet 3: 异常与查重清单
    ws_err = wb.create_sheet("异常与查重清单")
    ws_err.append(["源文件名", "状态", "发票号码", "价税合计", "问题详情"])
    for c in range(1, 6):
        cell = ws_err.cell(row=1, column=c)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for rec in records:
        if rec.status != "ok":
            detail = "；".join(i.message for i in rec.issues) or rec.duplicate_ref
            ws_err.append(
                [
                    sanitize_excel_cell(rec.file_name),
                    _STATUS_LABELS.get(rec.status, rec.status),
                    sanitize_excel_cell(rec.invoice_number),
                    _money(rec.total_amount),
                    sanitize_excel_cell(detail),
                ]
            )
    for c_idx, w in enumerate([28, 12, 22, 12, 55], start=1):
        ws_err.column_dimensions[get_column_letter(c_idx)].width = w

    return _atomic_save_workbook(wb, output_path)


def read_template_info(template_path: Path, sheet_name: str | None = None, header_row: int | None = None) -> dict[str, Any]:
    """读取企业 Excel 模板的工作表列表、表头行位置及各列名称预览。"""
    wb = load_workbook(str(template_path), read_only=True, data_only=True)
    try:
        sheets = list(wb.sheetnames)
        active_sheet = sheet_name if sheet_name and sheet_name in sheets else sheets[0]
        ws = wb[active_sheet]

        detected_row = 1
        columns: dict[str, str] = {}
        max_scan_rows = 10
        best_count = -1
        for r_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=max_scan_rows, max_col=26, values_only=True), start=1):
            if header_row is not None and r_idx != header_row:
                continue
            row_map = {}
            for c_idx, val in enumerate(row, start=1):
                if val is not None and str(val).strip():
                    row_map[get_column_letter(c_idx)] = str(val).strip()
            if header_row is not None:
                detected_row = header_row
                columns = row_map
                break
            if len(row_map) > best_count and len(row_map) >= 2:
                best_count = len(row_map)
                detected_row = r_idx
                columns = row_map
        return {
            "sheets": sheets,
            "sheet": active_sheet,
            "header_row": detected_row,
            "suggested_start_row": detected_row + 1,
            "columns": columns,
        }
    finally:
        wb.close()


def fill_company_template(
    records: list[InvoiceRecord],
    template_path: Path,
    mapping_config: dict[str, Any] | Path,
    output_path: Path,
    include_warnings: bool = True,
) -> tuple[Path, int]:
    """按用户自定义企业 Excel 报销模板与 JSON 映射配置自动填表，保留原表头、样式与公式。"""
    if isinstance(mapping_config, Path):
        cfg = json.loads(mapping_config.read_text(encoding="utf-8"))
    else:
        cfg = mapping_config

    wb = load_workbook(str(template_path))
    sheet_name = cfg.get("sheet")
    ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active
    start_row = int(cfg.get("start_row", 2))
    col_map: dict[str, str] = cfg.get("columns") or {}

    allowed = ("ok", "warning") if include_warnings else ("ok",)
    valid = [r for r in records if r.status in allowed]

    for offset, rec in enumerate(valid):
        row_num = start_row + offset
        data = rec.to_dict()
        for field_name, col_letter in col_map.items():
            if not col_letter:
                continue
            val: Any = data.get(field_name, "")
            if field_name in ("amount_excl_tax", "tax_amount", "total_amount") and val != "":
                val = float(val)
            else:
                val = sanitize_excel_cell(val)
            ws[f"{col_letter}{row_num}"] = val

    _atomic_save_workbook(wb, output_path)
    return output_path, len(valid)
