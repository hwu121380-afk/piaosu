"""核心领域模型：使用 Decimal 处理财务金额，避免浮点误差。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any


def to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"))
    text = (
        str(value)
        .strip()
        .replace(",", "")
        .replace("¥", "")
        .replace("￥", "")
        .replace("元", "")
        .replace(" ", "")
    )
    if not text or text in {"***", "**", "*", "--", "-", "免税", "不征税"}:
        return None
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def to_tax_decimal(value: Any) -> Decimal | None:
    """专门解析税额字段：将免税票据中的 *** / 免税 / 不征税 规范为 0.00。"""
    if value is None:
        return None
    raw = str(value).strip()
    if raw in {"***", "**", "*", "免税", "不征税"}:
        return Decimal("0.00")
    return to_decimal(raw)


@dataclass
class ValidationIssue:
    code: str
    severity: str  # "error" | "warning"
    message: str


@dataclass
class InvoiceRecord:
    file_path: str
    file_name: str
    file_sha256: str
    parse_channel: str  # "xml" | "ofd_xml" | "ofd_text" | "pdf_vector" | "ocr" | "failed"
    confidence: float = 1.0

    invoice_type: str = ""
    invoice_code: str = ""
    invoice_number: str = ""
    issue_date: str = ""  # YYYY-MM-DD

    buyer_name: str = ""
    buyer_tax_id: str = ""
    seller_name: str = ""
    seller_tax_id: str = ""

    item_summary: str = ""
    amount_excl_tax: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal | None = None
    tax_rate: str = ""
    remark: str = ""

    status: str = "ok"  # "ok" | "warning" | "duplicate_batch" | "duplicate_history" | "error"
    duplicate_ref: str = ""
    issues: list[ValidationIssue] = field(default_factory=list)
    manually_edited: bool = False

    @property
    def dedup_key(self) -> str:
        """用于跨月查重的唯一键：优先（发票代码+发票号码），数电票无代码时使用20位发票号码。"""
        num = self.invoice_number.strip()
        code = self.invoice_code.strip()
        if num:
            return f"{code}:{num}" if code else num
        return f"sha256:{self.file_sha256}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for money_field in ("amount_excl_tax", "tax_amount", "total_amount"):
            val = getattr(self, money_field)
            data[money_field] = f"{val:.2f}" if val is not None else ""
        return data


@dataclass
class BatchSummary:
    total_files: int = 0
    parsed_ok: int = 0
    warning_count: int = 0
    duplicate_batch_count: int = 0
    duplicate_history_count: int = 0
    error_count: int = 0
    valid_total_amount: Decimal = Decimal("0.00")
    valid_tax_amount: Decimal = Decimal("0.00")
    elapsed_ms: float = 0.0
    channel_counts: dict[str, int] = field(default_factory=dict)
