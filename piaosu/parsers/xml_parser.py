"""中国数电票原生 XML 解析器（含 XXE/实体炸弹防护 + 增值税/铁路客票/航空行程单全支持）。"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

from piaosu.models import InvoiceRecord, to_decimal, to_tax_decimal
from piaosu.parsers.rules import normalize_date

MAX_XML_BYTES = 10 * 1024 * 1024  # 10 MiB
_FORBIDDEN_XML_RE = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


def safe_fromstring(raw_bytes: bytes) -> ET.Element:
    """带体积限制与 XXE/Billion-Laughs 防护的安全 XML 解析。"""
    if len(raw_bytes) > MAX_XML_BYTES:
        raise ValueError(f"XML 文件体积超过安全上限（{MAX_XML_BYTES // (1024 * 1024)} MiB），已拒绝解析。")
    if _FORBIDDEN_XML_RE.search(raw_bytes[:65536]):
        raise ValueError("检测到不安全的 XML DOCTYPE/ENTITY 声明，已拒绝解析（防止 XXE / 实体扩展攻击）。")
    return ET.fromstring(raw_bytes)


def _strip_ns(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _find_text(root: ET.Element, *candidate_tags: str) -> str:
    wanted = {t.lower() for t in candidate_tags}
    for elem in root.iter():
        if _strip_ns(elem.tag).lower() in wanted and elem.text and elem.text.strip():
            return elem.text.strip()
    return ""


def _find_all_text(root: ET.Element, *candidate_tags: str) -> list[str]:
    wanted = {t.lower() for t in candidate_tags}
    results = []
    for elem in root.iter():
        if _strip_ns(elem.tag).lower() in wanted and elem.text and elem.text.strip():
            results.append(elem.text.strip())
    return results


def parse_xml_bytes(raw_bytes: bytes, record: InvoiceRecord, channel: str = "xml") -> InvoiceRecord:
    root = safe_fromstring(raw_bytes)
    record.parse_channel = channel
    record.confidence = 1.0

    # 数电票标准字段（增值税、铁路电子客票、航空行程单）
    raw_type = _find_text(root, "TypeName", "InvoiceTypeName", "LabelName", "GeneralOrSpecialVATLabelName")
    train_no = _find_text(root, "TrainNumber", "TrainNo")
    flight_no = _find_text(root, "FlightNumber", "FlightNo")
    if not raw_type:
        if train_no:
            raw_type = "铁路电子客票"
        elif flight_no:
            raw_type = "航空运输电子客票行程单"
        else:
            raw_type = "电子发票"
    record.invoice_type = raw_type

    record.invoice_number = _find_text(root, "InvoiceNumber", "InvoiceNo", "ElectronicTicketNum", "Fphm", "EIid")
    record.invoice_code = _find_text(root, "InvoiceCode", "Fpdm")
    raw_date = _find_text(root, "IssueTime", "IssueDate", "DateOfIssue", "Kprq")
    record.issue_date = normalize_date(raw_date) or (raw_date[:10] if len(raw_date) >= 10 else "")

    # 购销方信息（区分 SellerInformation 与 BuyerInformation 子树）
    for elem in root.iter():
        name = _strip_ns(elem.tag).lower()
        if name in ("sellerinformation", "seller"):
            record.seller_name = _find_text(elem, "SellerName", "Name") or record.seller_name
            record.seller_tax_id = _find_text(elem, "SellerIdNum", "TaxID", "SellerTaxNo") or record.seller_tax_id
        elif name in ("buyerinformation", "buyer"):
            record.buyer_name = _find_text(elem, "BuyerName", "Name") or record.buyer_name
            record.buyer_tax_id = _find_text(elem, "BuyerIdNum", "TaxID", "BuyerTaxNo") or record.buyer_tax_id

    if not record.seller_name:
        record.seller_name = _find_text(root, "SellerName", "XsfMc", "IssuingCompany")
    if not record.seller_name and "铁路" in record.invoice_type:
        record.seller_name = "中国国家铁路集团有限公司"
    if not record.seller_tax_id:
        record.seller_tax_id = _find_text(root, "SellerIdNum", "XsfNsrsbh")
    if not record.buyer_name:
        record.buyer_name = _find_text(root, "BuyerName", "GmfMc", "PurchaserName")
    if not record.buyer_tax_id:
        record.buyer_tax_id = _find_text(root, "BuyerIdNum", "GmfNsrsbh", "PurchaserTaxNo")

    record.amount_excl_tax = to_decimal(_find_text(root, "TotalAmWithoutTax", "AmountWithoutTax", "Hjje", "Fare"))
    record.tax_amount = to_tax_decimal(_find_text(root, "TotalTaxAm", "TaxAmount", "Hjse", "VATAmount"))
    record.total_amount = to_decimal(
        _find_text(root, "TotalTax-includedAmount", "TotalTaxIncludedAmount", "TotalAmount", "Jshj", "TicketPrice")
    )
    record.tax_rate = _find_text(root, "TaxRate", "Slv")
    record.remark = _find_text(root, "Remark", "Bz")

    # 铁路/航空客票金额补全（如仅含票价含税总额，自动按9%拆算不含税与税额）
    if "铁路" in record.invoice_type and record.total_amount is not None:
        if record.amount_excl_tax is None and record.tax_amount is None:
            record.tax_rate = record.tax_rate or "9%"
            record.amount_excl_tax = (record.total_amount / Decimal("1.09")).quantize(Decimal("0.01"))
            record.tax_amount = (record.total_amount - record.amount_excl_tax).quantize(Decimal("0.01"))
    elif record.total_amount is not None and record.amount_excl_tax is not None and record.tax_amount is None:
        record.tax_amount = (record.total_amount - record.amount_excl_tax).quantize(Decimal("0.01"))
    elif record.total_amount is None and record.amount_excl_tax is not None and record.tax_amount is not None:
        record.total_amount = (record.amount_excl_tax + record.tax_amount).quantize(Decimal("0.01"))

    items = _find_all_text(root, "ItemName", "GoodsName", "Hwlwmc")
    if items:
        record.item_summary = "；".join(items[:3]) + (f" 等{len(items)}项" if len(items) > 3 else "")
    elif train_no:
        dep = _find_text(root, "DepartureStation", "FromStation")
        arr = _find_text(root, "ArrivalStation", "ToStation")
        ride = _find_text(root, "RideDate", "DepartureDate")
        parts = [p for p in (f"{dep}→{arr}" if dep or arr else "", train_no, ride) if p]
        record.item_summary = "铁路客票 " + " ".join(parts)
    elif flight_no:
        dep = _find_text(root, "DepartureAirport", "FromCity")
        arr = _find_text(root, "ArrivalAirport", "ToCity")
        parts = [p for p in (f"{dep}→{arr}" if dep or arr else "", flight_no) if p]
        record.item_summary = "航空行程 " + " ".join(parts)

    return record


def parse_xml_file(path: Path, record: InvoiceRecord) -> InvoiceRecord:
    return parse_xml_bytes(path.read_bytes(), record, channel="xml")
