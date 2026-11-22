"""中文发票文本字段抽取规则（支持数电票双栏排版、传统增值税、红字负数、免税 ***、铁路电子客票与航空行程单）。"""

from __future__ import annotations

import re
from decimal import Decimal

from piaosu.models import InvoiceRecord, to_decimal, to_tax_decimal

_DATE_RE = re.compile(r"(20\d{2})\s*[年\-/\.]\s*(\d{1,2})\s*[月\-/\.]\s*(\d{1,2})\s*日?")
_INVOICE_NUM_20_RE = re.compile(r"(?:发票号码|电子客票号)\s*[:：]?\s*(\d{20})(?!\d)")
_INVOICE_NUM_SHORT_RE = re.compile(r"(?:发票号码|电子客票号)\s*[:：]?\s*(\d{8,20})(?!\d)")
_INVOICE_CODE_RE = re.compile(r"发票代码\s*[:：]?\s*(\d{10,12})")
_SIGNED_MONEY = r"(-?\s*[¥￥]?\s*-?\s*[0-9,]+\.\d{2})"
_MONEY_WITH_SYM_RE = re.compile(r"(-?\s*[¥￥]\s*-?\s*[0-9,]+\.\d{2})")
_TAX_RATE_RE = re.compile(r"(\d{1,2}%|免税|不征税|\*\*\*)")


def normalize_date(raw: str) -> str:
    m = _DATE_RE.search(raw or "")
    if not m:
        return ""
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if 1 <= mo <= 12 and 1 <= d <= 31:
        return f"{y:04d}-{mo:02d}-{d:02d}"
    return ""


def detect_invoice_type(text: str) -> str:
    if "铁路电子客票" in text or ("电子客票" in text and any(k in text for k in ("车次", "二等座", "一等座", "商务座", "硬卧", "站"))):
        return "铁路电子客票"
    if "航空运输电子客票行程单" in text or ("行程单" in text and "民航发展基金" in text):
        return "航空运输电子客票行程单"
    if "电子发票（增值税专用发票）" in text or "增值税专用发票" in text:
        return "电子发票（增值税专用发票）"
    if "电子发票（普通发票）" in text or "增值税电子普通发票" in text:
        return "电子发票（普通发票）"
    if "增值税普通发票" in text:
        return "增值税普通发票"
    return "电子发票"


def _extract_names_and_tax_ids(cleaned: str) -> tuple[str, str, str, str]:
    buyer_name = ""
    seller_name = ""
    buyer_tax = ""
    seller_tax = ""

    # 1. 显式“购买方名称：... / 销售方名称：...”
    m_buyer = re.search(r"(?:购\s*买\s*方|购方)[^\r\n：:]{0,12}?名称\s*[:：]\s*([^\r\n]+)", cleaned)
    m_seller = re.search(r"(?:销\s*售\s*方|销方)[^\r\n：:]{0,12}?名称\s*[:：]\s*([^\r\n]+)", cleaned)
    if m_buyer:
        buyer_name = re.split(r"\s+(?:销\s*售\s*方|销方|统一社会信用代码|纳税人识别号)", m_buyer.group(1))[0].strip()
    if m_seller:
        seller_name = re.split(r"\s+(?:统一社会信用代码|纳税人识别号)", m_seller.group(1))[0].strip()

    # 2. 双栏同行或分行的多个“名称：XXX”提取（例如数电票 PDF 常见的 `名称：购方公司  名称：销方公司`）
    if not buyer_name or not seller_name:
        inline_names = re.findall(
            r"名\s*称\s*[:：]\s*([^\r\n：:]+?)(?=\s+(?:销\s*售\s*方|购\s*买\s*方|名\s*称|统一社会信用代码|纳税人识别号)|$)",
            cleaned,
            flags=re.MULTILINE,
        )
        cleaned_names = [n.strip() for n in inline_names if n.strip()]
        if cleaned_names and not buyer_name:
            buyer_name = cleaned_names[0]
        if len(cleaned_names) >= 2 and not seller_name:
            seller_name = cleaned_names[1]

    # 3. 统一社会信用代码 / 纳税人识别号（支持同行双栏或分行）
    tax_ids: list[str] = []
    for m in re.finditer(r"(?:统一社会信用代码/纳税人识别号|纳税人识别号|统一社会信用代码)\s*[:：]\s*([0-9A-Za-z]{15,20})", cleaned):
        tid = m.group(1).strip().upper()
        if tid:
            tax_ids.append(tid)
    if tax_ids:
        buyer_tax = tax_ids[0]
    if len(tax_ids) >= 2:
        seller_tax = tax_ids[1]

    return buyer_name, buyer_tax, seller_name, seller_tax


def _extract_amounts(text: str, invoice_type: str) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    total: Decimal | None = None
    excl: Decimal | None = None
    tax: Decimal | None = None

    # 1. 铁路电子客票优先匹配“票价：¥123.00”
    if "铁路" in invoice_type:
        m_rail = re.search(r"票\s*价\s*[:：]?\s*" + _SIGNED_MONEY, text)
        if m_rail:
            total = to_decimal(m_rail.group(1))
        m_rail_tax = re.search(r"(?:增值税)?税\s*额\s*[:：]?\s*" + _SIGNED_MONEY, text)
        if m_rail_tax:
            tax = to_decimal(m_rail_tax.group(1))
        if total is not None and tax is None:
            excl = (total / Decimal("1.09")).quantize(Decimal("0.01"))
            tax = (total - excl).quantize(Decimal("0.01"))
        elif total is not None and tax is not None:
            excl = (total - tax).quantize(Decimal("0.01"))
        return excl, tax, total

    # 2. 价税合计（小写）或总计
    m_total = re.search(r"(?:价税合计|小\s*写)[^\r\n0-9¥￥\-]{0,20}" + _SIGNED_MONEY, text)
    if m_total:
        total = to_decimal(m_total.group(1))

    # 3. 合计行：支持“合 计 ¥100.00 ¥6.00”以及免税“合 计 ¥100.00 ***”
    m_heji_both = re.search(
        r"合\s*计[^\r\n0-9¥￥\-]{0,15}" + _SIGNED_MONEY + r"[^\r\n0-9¥￥\-\*]{1,20}" + _SIGNED_MONEY,
        text,
    )
    if m_heji_both:
        excl = to_decimal(m_heji_both.group(1))
        tax = to_decimal(m_heji_both.group(2))
    else:
        m_heji_exempt = re.search(
            r"合\s*计[^\r\n0-9¥￥\-]{0,15}" + _SIGNED_MONEY + r"[^\r\n]{0,15}(\*{2,3}|免税|不征税)",
            text,
        )
        if m_heji_exempt:
            excl = to_decimal(m_heji_exempt.group(1))
            tax = Decimal("0.00")
        else:
            m_excl = re.search(r"不含税金额\s*[:：]?\s*" + _SIGNED_MONEY, text)
            m_tax = re.search(r"(?:增值税)?税\s*额\s*[:：]?\s*(" + _SIGNED_MONEY + r"|\*{2,3}|免税|不征税)", text)
            if m_excl:
                excl = to_decimal(m_excl.group(1))
            if m_tax:
                tax = to_tax_decimal(m_tax.group(1))

    # 4. 兜底提取带货币符号的最大绝对值金额
    if total is None:
        all_money = [to_decimal(m.group(1)) for m in _MONEY_WITH_SYM_RE.finditer(text)]
        all_money = [v for v in all_money if v is not None]
        if all_money:
            total = max(all_money, key=lambda d: abs(d))

    # 5. 三缺一自动补算
    if total is None and excl is not None and tax is not None:
        total = (excl + tax).quantize(Decimal("0.01"))
    elif excl is None and total is not None and tax is not None:
        excl = (total - tax).quantize(Decimal("0.01"))
    elif tax is None and total is not None and excl is not None:
        tax = (total - excl).quantize(Decimal("0.01"))

    return excl, tax, total


def extract_from_text(text: str, record: InvoiceRecord) -> InvoiceRecord:
    cleaned = text.replace("\u3000", " ").strip()
    record.invoice_type = detect_invoice_type(cleaned)

    m_num20 = _INVOICE_NUM_20_RE.search(cleaned)
    if m_num20:
        record.invoice_number = m_num20.group(1)
    else:
        m_num_short = _INVOICE_NUM_SHORT_RE.search(cleaned)
        if m_num_short:
            record.invoice_number = m_num_short.group(1)

    m_code = _INVOICE_CODE_RE.search(cleaned)
    if m_code:
        record.invoice_code = m_code.group(1)

    m_date = re.search(r"开票日期\s*[:：]?\s*([^\r\n]+)", cleaned)
    if m_date:
        record.issue_date = normalize_date(m_date.group(1))
    if not record.issue_date:
        record.issue_date = normalize_date(cleaned)

    b_name, b_tax, s_name, s_tax = _extract_names_and_tax_ids(cleaned)
    record.buyer_name = b_name
    record.buyer_tax_id = b_tax
    record.seller_name = s_name
    record.seller_tax_id = s_tax
    if not record.seller_name and "铁路" in record.invoice_type:
        record.seller_name = "中国国家铁路集团有限公司"

    excl, tax, total = _extract_amounts(cleaned, record.invoice_type)
    record.amount_excl_tax = excl
    record.tax_amount = tax
    record.total_amount = total

    if "铁路" in record.invoice_type:
        record.tax_rate = "9%"
        m_train = re.search(r"([GDCZKT]\d{1,5})", cleaned)
        m_route = re.search(r"([\u4e00-\u9fa5]{2,8}站?)\s*(?:->|→|至|-)\s*([\u4e00-\u9fa5]{2,8}站?)", cleaned)
        parts = []
        if m_route:
            parts.append(f"{m_route.group(1)}→{m_route.group(2)}")
        if m_train:
            parts.append(m_train.group(1))
        record.item_summary = "铁路客票 " + " ".join(parts) if parts else "铁路电子客票"
    else:
        m_rate = _TAX_RATE_RE.search(cleaned)
        if m_rate:
            val = m_rate.group(1)
            record.tax_rate = "免税" if val == "***" else val
        m_item = re.search(r"(\*[^*\r\n]{1,20}\*[^\r\n\t]{1,60})", cleaned)
        if m_item:
            record.item_summary = m_item.group(1).strip()

    m_remark = re.search(r"备\s*注\s*[:：]\s*([^\r\n]+)", cleaned)
    if m_remark:
        record.remark = m_remark.group(1).strip()

    return record
