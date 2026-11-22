"""程序化合成测试用中国数电票 XML、铁路电子客票、航空行程单、OFD 与矢量 PDF（含双栏、红字、免税、加密与畸形样本）。"""

from __future__ import annotations

import zipfile
from pathlib import Path


def make_einvoice_xml(
    path: Path,
    *,
    invoice_number: str = "26112000000123456789",
    issue_date: str = "2026-11-18",
    seller_name: str = "北京星海云计算有限公司",
    seller_tax_id: str = "91110108MA01ABCD1X",
    buyer_name: str = "上海青云科技有限公司",
    buyer_tax_id: str = "91310115MA1K998877",
    excl: str = "1000.00",
    tax: str = "60.00",
    total: str = "1060.00",
    item_name: str = "*信息技术服务*云主机租用费",
    tax_rate: str = "6%",
    remark: str = "差旅项目测试",
) -> Path:
    xml_text = f"""<?xml version="1.0" encoding="UTF-8"?>
<EInvoice>
  <Header>
    <EIid>{invoice_number}</EIid>
    <InherentLabel>
      <GeneralOrSpecialVAT>
        <LabelName>电子发票（普通发票）</LabelName>
      </GeneralOrSpecialVAT>
    </InherentLabel>
  </Header>
  <EInvoiceData>
    <SellerInformation>
      <SellerIdNum>{seller_tax_id}</SellerIdNum>
      <SellerName>{seller_name}</SellerName>
    </SellerInformation>
    <BuyerInformation>
      <BuyerIdNum>{buyer_tax_id}</BuyerIdNum>
      <BuyerName>{buyer_name}</BuyerName>
    </BuyerInformation>
    <BasicInformation>
      <TotalAmWithoutTax>{excl}</TotalAmWithoutTax>
      <TotalTaxAm>{tax}</TotalTaxAm>
      <TotalTax-includedAmount>{total}</TotalTax-includedAmount>
      <Remark>{remark}</Remark>
    </BasicInformation>
    <IssuItemInformation>
      <ItemName>{item_name}</ItemName>
      <TaxRate>{tax_rate}</TaxRate>
    </IssuItemInformation>
  </EInvoiceData>
  <TaxSupervisionInfo>
    <InvoiceNumber>{invoice_number}</InvoiceNumber>
    <IssueTime>{issue_date}</IssueTime>
  </TaxSupervisionInfo>
</EInvoice>
"""
    path.write_text(xml_text, encoding="utf-8")
    return path


def make_railway_xml(
    path: Path,
    *,
    ticket_number: str = "26112000000888777666",
    issue_date: str = "2026-11-18",
    train_no: str = "G1",
    dep: str = "北京南",
    arr: str = "上海虹桥",
    ride_date: str = "2026-11-18",
    price: str = "553.00",
    buyer_name: str = "上海青云科技有限公司",
    buyer_tax_id: str = "91310115MA1K998877",
) -> Path:
    xml_text = f"""<?xml version="1.0" encoding="UTF-8"?>
<RailwayEInvoice>
  <ElectronicTicketNum>{ticket_number}</ElectronicTicketNum>
  <DateOfIssue>{issue_date}</DateOfIssue>
  <TrainNumber>{train_no}</TrainNumber>
  <DepartureStation>{dep}</DepartureStation>
  <ArrivalStation>{arr}</ArrivalStation>
  <RideDate>{ride_date}</RideDate>
  <TicketPrice>{price}</TicketPrice>
  <PurchaserName>{buyer_name}</PurchaserName>
  <PurchaserTaxNo>{buyer_tax_id}</PurchaserTaxNo>
</RailwayEInvoice>
"""
    path.write_text(xml_text, encoding="utf-8")
    return path


def make_flight_xml(
    path: Path,
    *,
    invoice_number: str = "26112000000777666555",
    issue_date: str = "2026-11-19",
    airline: str = "中国国际航空股份有限公司",
    airline_tax_id: str = "911100007178710060",
    buyer_name: str = "上海青云科技有限公司",
    buyer_tax_id: str = "91310115MA1K998877",
    flight_no: str = "CA1501",
    dep: str = "PEK",
    arr: str = "SHA",
    fare: str = "1200.00",
    vat: str = "108.00",
    total: str = "1308.00",
) -> Path:
    xml_text = f"""<?xml version="1.0" encoding="UTF-8"?>
<AirEInvoice>
  <TypeName>航空运输电子客票行程单</TypeName>
  <InvoiceNumber>{invoice_number}</InvoiceNumber>
  <IssueDate>{issue_date}</IssueDate>
  <IssuingCompany>{airline}</IssuingCompany>
  <SellerTaxNo>{airline_tax_id}</SellerTaxNo>
  <PurchaserName>{buyer_name}</PurchaserName>
  <PurchaserTaxNo>{buyer_tax_id}</PurchaserTaxNo>
  <FlightNumber>{flight_no}</FlightNumber>
  <DepartureAirport>{dep}</DepartureAirport>
  <ArrivalAirport>{arr}</ArrivalAirport>
  <Fare>{fare}</Fare>
  <VATAmount>{vat}</VATAmount>
  <TotalAmount>{total}</TotalAmount>
</AirEInvoice>
"""
    path.write_text(xml_text, encoding="utf-8")
    return path


def make_ofd_with_embedded_xml(path: Path, **xml_kwargs) -> Path:
    tmp_xml = path.with_suffix(".tmp.xml")
    make_einvoice_xml(tmp_xml, **xml_kwargs)
    xml_bytes = tmp_xml.read_bytes()
    tmp_xml.unlink()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("OFD.xml", '<?xml version="1.0" encoding="UTF-8"?><ofd:OFD Version="1.1"/>')
        zf.writestr("Doc_0/Attachments/original_invoice.xml", xml_bytes)
    return path


def make_ofd_textcode_split_boundary(
    path: Path,
    *,
    invoice_number: str = "26112000000987654321",
    issue_date: str = "2026年11月19日",
    seller_name: str = "深圳光速交通科技有限公司",
    buyer_name: str = "上海青云科技有限公司",
    excl: str = "200.00",
    tax: str = "12.00",
    total: str = "212.00",
) -> Path:
    """刻意把标签和值拆分成独立 TextObject，且打乱 DOM 顺序，验证 Boundary 坐标行重组能力。"""
    content_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<ofd:Page xmlns:ofd="http://www.ofdspec.org/2016">
  <ofd:Content>
    <ofd:Layer ID="1">
      <ofd:TextObject ID="90" Boundary="140 20 60 5"><ofd:TextCode>{invoice_number}</ofd:TextCode></ofd:TextObject>
      <ofd:TextObject ID="1" Boundary="20 10 80 5"><ofd:TextCode>电子发票（普通发票）</ofd:TextCode></ofd:TextObject>
      <ofd:TextObject ID="2" Boundary="110 20 30 5"><ofd:TextCode>发票号码：</ofd:TextCode></ofd:TextObject>
      <ofd:TextObject ID="3" Boundary="110 28 30 5"><ofd:TextCode>开票日期：</ofd:TextCode></ofd:TextObject>
      <ofd:TextObject ID="4" Boundary="142 28 40 5"><ofd:TextCode>{issue_date}</ofd:TextCode></ofd:TextObject>
      <ofd:TextObject ID="5" Boundary="20 42 90 5"><ofd:TextCode>购买方名称：{buyer_name}</ofd:TextCode></ofd:TextObject>
      <ofd:TextObject ID="6" Boundary="120 42 90 5"><ofd:TextCode>销售方名称：{seller_name}</ofd:TextCode></ofd:TextObject>
      <ofd:TextObject ID="7" Boundary="20 65 90 5"><ofd:TextCode>*运输服务*客运服务费</ofd:TextCode></ofd:TextObject>
      <ofd:TextObject ID="8" Boundary="20 80 60 5"><ofd:TextCode>合 计 ¥{excl}</ofd:TextCode></ofd:TextObject>
      <ofd:TextObject ID="9" Boundary="120 80 40 5"><ofd:TextCode>¥{tax}</ofd:TextCode></ofd:TextObject>
      <ofd:TextObject ID="10" Boundary="20 92 90 5"><ofd:TextCode>价税合计（小写）</ofd:TextCode></ofd:TextObject>
      <ofd:TextObject ID="11" Boundary="130 92 40 5"><ofd:TextCode>¥{total}</ofd:TextCode></ofd:TextObject>
    </ofd:Layer>
  </ofd:Content>
</ofd:Page>
"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("OFD.xml", '<?xml version="1.0" encoding="UTF-8"?><ofd:OFD Version="1.1"/>')
        zf.writestr("Doc_0/Pages/Page_0/Content.xml", content_xml.encode("utf-8"))
    return path


def make_vector_pdf_invoice(
    path: Path,
    *,
    invoice_number: str = "26112000000555666777",
    issue_date: str = "2026年11月20日",
    seller_name: str = "杭州云帆酒店管理有限公司",
    seller_tax_id: str = "91330106MA28ABCD88",
    buyer_name: str = "上海青云科技有限公司",
    buyer_tax_id: str = "91310115MA1K998877",
    excl: str = "500.00",
    tax: str = "30.00",
    total: str = "530.00",
    two_column: bool = False,
    tax_exempt: bool = False,
    encrypt_password: str | None = None,
) -> Path:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=610, height=394)
    if two_column:
        # 真实数电票常见的左右双栏结构
        page.insert_text((36, 40), "电子发票（普通发票）", fontname="china-ss", fontsize=14)
        page.insert_text((310, 34), f"发票号码：{invoice_number}", fontname="china-ss", fontsize=10)
        page.insert_text((310, 52), f"开票日期：{issue_date}", fontname="china-ss", fontsize=10)
        page.insert_text((36, 90), f"名称：{buyer_name}", fontname="china-ss", fontsize=10)
        page.insert_text((280, 90), f"名称：{seller_name}", fontname="china-ss", fontsize=10)
        page.insert_text((36, 112), f"统一社会信用代码/纳税人识别号：{buyer_tax_id}", fontname="china-ss", fontsize=9)
        page.insert_text((280, 112), f"统一社会信用代码/纳税人识别号：{seller_tax_id}", fontname="china-ss", fontsize=9)
        page.insert_text((36, 160), "*餐饮服务*餐费  免税" if tax_exempt else "*住宿服务*房费  6%", fontname="china-ss", fontsize=11)
        tax_str = "***" if tax_exempt else f"¥{tax}"
        page.insert_text((36, 220), f"合 计 ¥{excl}   {tax_str}", fontname="china-ss", fontsize=11)
        page.insert_text((36, 260), f"价税合计（小写） ¥{total}", fontname="china-ss", fontsize=12)
    else:
        tax_str = "***" if tax_exempt else f"¥{tax}"
        lines = [
            "电子发票（普通发票）",
            f"发票号码：{invoice_number}",
            f"开票日期：{issue_date}",
            f"购买方名称：{buyer_name}",
            f"统一社会信用代码/纳税人识别号：{buyer_tax_id}",
            f"销售方名称：{seller_name}",
            f"统一社会信用代码/纳税人识别号：{seller_tax_id}",
            "*住宿服务*房费  6%" if not tax_exempt else "*免税服务*培训费  免税",
            f"合 计 ¥{excl} {tax_str}",
            f"价税合计（小写） ¥{total}",
            "备注：出差住宿",
        ]
        y = 40
        for line in lines:
            page.insert_text((36, y), line, fontname="china-ss", fontsize=12)
            y += 28

    if encrypt_password:
        doc.save(
            str(path),
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw=encrypt_password,
            owner_pw=encrypt_password + "_owner",
        )
    else:
        doc.save(str(path))
    doc.close()
    return path


def make_railway_pdf_invoice(
    path: Path,
    *,
    ticket_number: str = "26112000000666555444",
    issue_date: str = "2026年11月21日",
    price: str = "218.00",
    train_no: str = "G102",
    route: str = "上海虹桥站 → 北京南站",
) -> Path:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=610, height=394)
    lines = [
        "电子发票（铁路电子客票）",
        f"发票号码：{ticket_number}",
        f"开票日期：{issue_date}",
        f"{route}  {train_no}  二等座",
        f"票价：¥{price}",
        "购买方名称：上海青云科技有限公司",
        "统一社会信用代码/纳税人识别号：91310115MA1K998877",
    ]
    y = 40
    for line in lines:
        page.insert_text((36, y), line, fontname="china-ss", fontsize=12)
        y += 32
    doc.save(str(path))
    doc.close()
    return path
