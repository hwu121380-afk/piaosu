"""财务勾稽与合规校验引擎（使用 Decimal 精确校验价税合计、税号、发票号与 OCR 置信度）。"""

from __future__ import annotations

import re
from decimal import Decimal

from piaosu.models import InvoiceRecord, ValidationIssue

_TAX_ID_PATTERN = re.compile(r"^[0-9A-HJ-NPQRTUWXY]{15,20}$")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_invoice(record: InvoiceRecord) -> InvoiceRecord:
    if record.parse_channel == "failed" and not record.manually_edited:
        record.status = "error"
        return record

    issues = list(record.issues)

    if not record.invoice_number:
        issues.append(ValidationIssue("missing_invoice_number", "error", "缺少发票号码"))
    elif len(record.invoice_number) not in (8, 10, 12, 20):
        issues.append(
            ValidationIssue(
                "unusual_invoice_number",
                "warning",
                f"发票号码长度非常规（{len(record.invoice_number)} 位）：{record.invoice_number}",
            )
        )

    if record.total_amount is None:
        issues.append(ValidationIssue("missing_total_amount", "error", "缺少价税合计金额"))
    elif record.total_amount < Decimal("0.00"):
        issues.append(ValidationIssue("negative_amount", "warning", "金额为负数（疑似红字冲销发票，请人工核对）"))

    # 核心财务勾稽：不含税金额 + 税额 == 价税合计（允许 0.02 元四舍五入容差）
    if (
        record.amount_excl_tax is not None
        and record.tax_amount is not None
        and record.total_amount is not None
    ):
        diff = abs((record.amount_excl_tax + record.tax_amount) - record.total_amount)
        if diff > Decimal("0.02"):
            issues.append(
                ValidationIssue(
                    "amount_mismatch",
                    "error",
                    f"价税勾稽不一致：{record.amount_excl_tax} + {record.tax_amount} != {record.total_amount}（差额 {diff}）",
                )
            )

    if not record.issue_date or not _DATE_PATTERN.match(record.issue_date):
        issues.append(ValidationIssue("missing_or_bad_date", "warning", "缺少或无法规范化开票日期"))

    if not record.seller_name:
        issues.append(ValidationIssue("missing_seller", "warning", "未提取到销售方名称"))

    if record.buyer_tax_id and not _TAX_ID_PATTERN.match(record.buyer_tax_id):
        issues.append(ValidationIssue("invalid_buyer_tax_id", "warning", f"购方税号格式异常：{record.buyer_tax_id}"))
    if record.seller_tax_id and not _TAX_ID_PATTERN.match(record.seller_tax_id):
        issues.append(ValidationIssue("invalid_seller_tax_id", "warning", f"销方税号格式异常：{record.seller_tax_id}"))

    if record.parse_channel == "ocr" and not record.manually_edited and record.confidence < 0.85:
        issues.append(
            ValidationIssue(
                "low_ocr_confidence",
                "warning",
                f"OCR 置信度偏低（{record.confidence:.2f}），建议人工复核金额与票号",
            )
        )

    record.issues = issues
    if any(i.severity == "error" for i in issues):
        record.status = "error"
    elif issues:
        record.status = "warning"
    else:
        record.status = "ok"
    return record
