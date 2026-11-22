"""中国国标 OFD 电子发票原生解析器（含 ZIP 炸弹/路径穿越防护 + 坐标感知行重组）。

解析策略：
1. 安全检查：限制压缩包条目数与解压总字节数，拦截路径穿越（Zip Slip）；
2. 优先查找 OFD 内嵌的原始数电票/税控 XML（如 Attachments/*.xml、Attachs/*.xml、original_invoice.xml）；
3. 若无内嵌发票 XML，按 TextObject 的 Boundary (x, y, w, h) 坐标重组视觉行，将同一行的
   分离 TextCode 节点按 X 坐标从左到右拼接后再交由规则引擎抽取字段。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from piaosu.models import InvoiceRecord
from piaosu.parsers.rules import extract_from_text
from piaosu.parsers.xml_parser import parse_xml_bytes, safe_fromstring

MAX_OFD_ENTRIES = 500
MAX_OFD_UNCOMPRESSED_BYTES = 20 * 1024 * 1024  # 20 MiB
_ROW_Y_TOLERANCE = 3.0  # mm


def _is_invoice_xml_candidate(name: str) -> bool:
    lower = name.lower().replace("\\", "/")
    if not lower.endswith(".xml"):
        return False
    base = lower.rsplit("/", 1)[-1]
    if base in {
        "ofd.xml",
        "document.xml",
        "documentres.xml",
        "publicres.xml",
        "content.xml",
        "signatures.xml",
        "signature.xml",
        "attachments.xml",
        "customtags.xml",
    }:
        return False
    return any(tok in lower for tok in ("invoice", "attach", "fapiao", "einvoice"))


def _validate_zip_safety(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = zf.infolist()
    if len(infos) > MAX_OFD_ENTRIES:
        raise ValueError(f"OFD 压缩包条目过多（{len(infos)} > {MAX_OFD_ENTRIES}），已拒绝解析。")
    total_size = 0
    for info in infos:
        normalized = info.filename.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError(f"OFD 包含非法路径条目：{info.filename}")
        total_size += info.file_size
        if total_size > MAX_OFD_UNCOMPRESSED_BYTES:
            raise ValueError("OFD 解压后体积超过安全上限（20 MiB），疑似压缩炸弹，已拒绝解析。")
    return infos


def _parse_boundary(raw: str | None) -> tuple[float, float]:
    if not raw:
        return (0.0, 0.0)
    parts = raw.strip().split()
    if len(parts) >= 2:
        try:
            return (float(parts[0]), float(parts[1]))
        except ValueError:
            return (0.0, 0.0)
    return (0.0, 0.0)


def _extract_visual_lines(xml_bytes: bytes) -> list[str]:
    root = safe_fromstring(xml_bytes)
    positioned: list[tuple[float, float, str]] = []
    unpositioned: list[str] = []

    for elem in root.iter():
        tag = elem.tag.split("}", 1)[-1] if "}" in elem.tag else elem.tag
        if tag == "TextObject":
            b_attr = elem.attrib.get("Boundary")
            texts = []
            for child in elem.iter():
                ctag = child.tag.split("}", 1)[-1] if "}" in child.tag else child.tag
                if ctag == "TextCode" and child.text and child.text.strip():
                    texts.append(child.text.strip())
            if not texts:
                continue
            combined = "".join(texts)
            if b_attr:
                x, y = _parse_boundary(b_attr)
                positioned.append((y, x, combined))
            else:
                unpositioned.append(combined)

    if not positioned:
        return unpositioned

    # 按视觉行（Y 坐标聚簇）从上到下、行内按 X 坐标从左到右重组
    positioned.sort(key=lambda item: (item[0], item[1]))
    rows: list[list[tuple[float, float, str]]] = []
    for item in positioned:
        if not rows or abs(item[0] - rows[-1][0][0]) > _ROW_Y_TOLERANCE:
            rows.append([item])
        else:
            rows[-1].append(item)

    lines: list[str] = []
    for row in rows:
        row.sort(key=lambda item: item[1])
        lines.append(" ".join(seg[2] for seg in row))
    lines.extend(unpositioned)
    return lines


def parse_ofd_file(path: Path, record: InvoiceRecord) -> InvoiceRecord:
    with zipfile.ZipFile(path, "r") as zf:
        infos = _validate_zip_safety(zf)
        names = [info.filename for info in infos]

        # 1. 优先尝试内嵌原始发票 XML
        for name in names:
            if _is_invoice_xml_candidate(name):
                try:
                    raw = zf.read(name)
                    parsed = parse_xml_bytes(raw, record, channel="ofd_xml")
                    if parsed.invoice_number or parsed.total_amount is not None:
                        return parsed
                except (ET.ParseError, ValueError):
                    continue

        # 2. 降级：从页面 Content.xml 按视觉坐标提取精确矢量文本
        page_texts: list[str] = []
        for name in sorted(names):
            lower = name.lower().replace("\\", "/")
            if lower.endswith("content.xml") or ("/pages/" in lower and lower.endswith(".xml")):
                try:
                    page_texts.extend(_extract_visual_lines(zf.read(name)))
                except (ET.ParseError, ValueError):
                    continue

    joined = "\n".join(page_texts)
    record.parse_channel = "ofd_text"
    record.confidence = 0.98 if joined else 0.0
    return extract_from_text(joined, record)
