"""票溯（PiaoSu）全链路单元与集成测试（覆盖解析精度、安全防注入、跨月查重、模板、归档、CLI 与 GUI）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook, load_workbook

from piaosu.exporter import (
    export_reimbursement_excel,
    fill_company_template,
    read_template_info,
    sanitize_excel_cell,
)
from piaosu.gui import EditRecordDialog, LedgerDialog, PiaoSuApp, TemplateMapperDialog
from piaosu.ledger import InvoiceLedger
from piaosu.merger import merge_valid_record_pdfs
from piaosu.parsers import parse_invoice_file
from piaosu.renamer import archive_rename_invoices, build_archive_filename, sanitize_filename_part
from piaosu.service import process_invoices
from piaosu.validator import validate_invoice
from tests.fixtures import (
    make_einvoice_xml,
    make_flight_xml,
    make_ofd_textcode_split_boundary,
    make_ofd_with_embedded_xml,
    make_railway_pdf_invoice,
    make_railway_xml,
    make_vector_pdf_invoice,
)


class ParserAndFormatTests(unittest.TestCase):
    def test_xml_native_parser(self):
        with tempfile.TemporaryDirectory() as tmp:
            xml_path = make_einvoice_xml(Path(tmp) / "inv1.xml")
            rec = validate_invoice(parse_invoice_file(xml_path))
            self.assertEqual(rec.parse_channel, "xml")
            self.assertEqual(rec.status, "ok")
            self.assertEqual(rec.invoice_number, "26112000000123456789")
            self.assertEqual(rec.issue_date, "2026-11-18")
            self.assertEqual(rec.seller_name, "北京星海云计算有限公司")
            self.assertEqual(rec.amount_excl_tax, Decimal("1000.00"))
            self.assertEqual(rec.tax_amount, Decimal("60.00"))
            self.assertEqual(rec.total_amount, Decimal("1060.00"))

    def test_negative_red_invoice_xml_and_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            red_xml = make_einvoice_xml(
                root / "red.xml",
                invoice_number="26112000000100000001",
                excl="-100.00",
                tax="-6.00",
                total="-106.00",
            )
            r1 = validate_invoice(parse_invoice_file(red_xml))
            self.assertEqual(r1.status, "warning")
            self.assertTrue(any(i.code == "negative_amount" for i in r1.issues))
            self.assertEqual(r1.total_amount, Decimal("-106.00"))

            red_pdf = make_vector_pdf_invoice(
                root / "red.pdf",
                invoice_number="26112000000100000002",
                excl="-200.00",
                tax="-12.00",
                total="-212.00",
            )
            r2 = validate_invoice(parse_invoice_file(red_pdf))
            self.assertEqual(r2.status, "warning")
            self.assertEqual(r2.amount_excl_tax, Decimal("-200.00"))
            self.assertEqual(r2.tax_amount, Decimal("-12.00"))
            self.assertEqual(r2.total_amount, Decimal("-212.00"))

    def test_railway_xml_and_pdf_9_percent_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            r_xml = make_railway_xml(root / "rail.xml", price="553.00")
            rec1 = validate_invoice(parse_invoice_file(r_xml))
            self.assertEqual(rec1.status, "ok")
            self.assertEqual(rec1.invoice_type, "铁路电子客票")
            self.assertEqual(rec1.seller_name, "中国国家铁路集团有限公司")
            self.assertEqual(rec1.total_amount, Decimal("553.00"))
            self.assertEqual(rec1.amount_excl_tax + rec1.tax_amount, rec1.total_amount)
            self.assertIn("G1", rec1.item_summary)

            r_pdf = make_railway_pdf_invoice(root / "rail.pdf", price="218.00")
            rec2 = validate_invoice(parse_invoice_file(r_pdf))
            self.assertEqual(rec2.status, "ok")
            self.assertEqual(rec2.invoice_type, "铁路电子客票")
            self.assertEqual(rec2.total_amount, Decimal("218.00"))
            self.assertEqual(rec2.amount_excl_tax + rec2.tax_amount, rec2.total_amount)
            self.assertIn("G102", rec2.item_summary)

    def test_flight_xml_parser(self):
        with tempfile.TemporaryDirectory() as tmp:
            f_xml = make_flight_xml(Path(tmp) / "air.xml")
            rec = validate_invoice(parse_invoice_file(f_xml))
            self.assertEqual(rec.status, "ok")
            self.assertIn("航空", rec.invoice_type)
            self.assertEqual(rec.seller_name, "中国国际航空股份有限公司")
            self.assertEqual(rec.total_amount, Decimal("1308.00"))
            self.assertIn("CA1501", rec.item_summary)

    def test_ofd_embedded_xml_and_split_boundary_reassembly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ofd_xml = make_ofd_with_embedded_xml(
                root / "embedded.ofd",
                invoice_number="26112000000222222222",
                excl="300.00",
                tax="18.00",
                total="318.00",
            )
            r1 = validate_invoice(parse_invoice_file(ofd_xml))
            self.assertEqual(r1.parse_channel, "ofd_xml")
            self.assertEqual(r1.status, "ok")
            self.assertEqual(r1.total_amount, Decimal("318.00"))

            ofd_split = make_ofd_textcode_split_boundary(root / "split.ofd")
            r2 = validate_invoice(parse_invoice_file(ofd_split))
            self.assertEqual(r2.parse_channel, "ofd_text")
            self.assertEqual(r2.status, "ok")
            self.assertEqual(r2.invoice_number, "26112000000987654321")
            self.assertEqual(r2.issue_date, "2026-11-19")
            self.assertEqual(r2.buyer_name, "上海青云科技有限公司")
            self.assertEqual(r2.seller_name, "深圳光速交通科技有限公司")
            self.assertEqual(r2.total_amount, Decimal("212.00"))

    def test_pdf_two_column_tax_exempt_encrypted_and_corrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            two_col = make_vector_pdf_invoice(
                root / "two_col.pdf",
                invoice_number="26112000000555666001",
                two_column=True,
                tax_exempt=True,
                excl="400.00",
                tax="0.00",
                total="400.00",
            )
            r1 = validate_invoice(parse_invoice_file(two_col))
            self.assertEqual(r1.status, "ok")
            self.assertEqual(r1.invoice_number, "26112000000555666001")
            self.assertEqual(r1.buyer_name, "上海青云科技有限公司")
            self.assertEqual(r1.seller_name, "杭州云帆酒店管理有限公司")
            self.assertEqual(r1.seller_tax_id, "91330106MA28ABCD88")
            self.assertEqual(r1.tax_amount, Decimal("0.00"))
            self.assertEqual(r1.tax_rate, "免税")

            enc_pdf = make_vector_pdf_invoice(root / "locked.pdf", encrypt_password="secret")
            r2 = validate_invoice(parse_invoice_file(enc_pdf))
            self.assertEqual(r2.status, "error")
            self.assertTrue(any(i.code == "pdf_unreadable" for i in r2.issues))

            corrupt_pdf = root / "bad.pdf"
            corrupt_pdf.write_bytes(b"NOT_A_REAL_PDF_FILE_CONTENT")
            r3 = validate_invoice(parse_invoice_file(corrupt_pdf))
            self.assertEqual(r3.status, "error")

    def test_financial_reconciliation_catches_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_xml = make_einvoice_xml(
                Path(tmp) / "bad.xml",
                excl="100.00",
                tax="6.00",
                total="999.00",
            )
            rec = validate_invoice(parse_invoice_file(bad_xml))
            self.assertEqual(rec.status, "error")
            self.assertTrue(any(i.code == "amount_mismatch" for i in rec.issues))


class SecurityTests(unittest.TestCase):
    def test_xxe_doctype_xml_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            evil_xml = Path(tmp) / "xxe.xml"
            evil_xml.write_text(
                '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
                "<EInvoice><InvoiceNumber>&xxe;</InvoiceNumber></EInvoice>",
                encoding="utf-8",
            )
            rec = validate_invoice(parse_invoice_file(evil_xml))
            self.assertEqual(rec.parse_channel, "failed")
            self.assertEqual(rec.status, "error")
            self.assertTrue(any("DOCTYPE" in i.message or "XXE" in i.message for i in rec.issues))

    def test_xml_size_limit_and_ofd_entry_limit(self):
        from piaosu.parsers.xml_parser import MAX_XML_BYTES, safe_fromstring

        with self.assertRaises(ValueError):
            safe_fromstring(b"<A>" + (b"x" * (MAX_XML_BYTES + 10)) + b"</A>")

        with tempfile.TemporaryDirectory() as tmp:
            too_many = Path(tmp) / "many.ofd"
            with zipfile.ZipFile(too_many, "w") as zf:
                for i in range(505):
                    zf.writestr(f"p/{i}.txt", "x")
            rec = validate_invoice(parse_invoice_file(too_many))
            self.assertEqual(rec.status, "error")
            self.assertTrue(any("条目过多" in i.message for i in rec.issues))

    def test_ofd_zip_slip_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            evil_ofd = Path(tmp) / "slip.ofd"
            with zipfile.ZipFile(evil_ofd, "w") as zf:
                zf.writestr("../../evil.xml", "<EInvoice/>")
            rec = validate_invoice(parse_invoice_file(evil_ofd))
            self.assertEqual(rec.parse_channel, "failed")
            self.assertEqual(rec.status, "error")
            self.assertTrue(any("非法路径" in i.message for i in rec.issues))

    def test_excel_formula_injection_sanitized(self):
        self.assertEqual(sanitize_excel_cell("=CMD|'/C calc'!A0"), "'=CMD|'/C calc'!A0")
        self.assertEqual(sanitize_excel_cell("+1+1"), "'+1+1")
        self.assertEqual(sanitize_excel_cell("-2+3"), "'-2+3")
        self.assertEqual(sanitize_excel_cell("@SUM(A1:A2)"), "'@SUM(A1:A2)")
        self.assertEqual(sanitize_excel_cell(123.45), 123.45)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evil_xml = make_einvoice_xml(
                root / "formula.xml",
                seller_name='=HYPERLINK("http://evil.example","点击")',
            )
            recs, summary = process_invoices([evil_xml], ledger=InvoiceLedger(root / "l.db"))
            xlsx = export_reimbursement_excel(recs, summary, root / "safe.xlsx")
            wb = load_workbook(str(xlsx))
            cell_val = wb["报销明细"].cell(row=2, column=8).value
            self.assertTrue(str(cell_val).startswith("'="))


class LedgerExporterRenamerCLITests(unittest.TestCase):
    def test_cross_month_dedup_rollback_and_json_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = InvoiceLedger(root / "ledger.db")
            f1 = make_einvoice_xml(root / "a.xml", invoice_number="26112000000111111111", excl="100.00", tax="6.00", total="106.00")
            make_einvoice_xml(root / "a_copy.xml", invoice_number="26112000000111111111", excl="100.00", tax="6.00", total="106.00")
            f3 = make_vector_pdf_invoice(root / "b.pdf", invoice_number="26112000000333333333", excl="200.00", tax="12.00", total="212.00")

            recs, summary = process_invoices([root], ledger=ledger)
            self.assertEqual(summary.total_files, 3)
            self.assertEqual(summary.parsed_ok, 2)
            self.assertEqual(summary.duplicate_batch_count, 1)
            self.assertEqual(summary.valid_total_amount, Decimal("318.00"))

            bid, committed = ledger.commit_batch(recs, note="2026-11差旅")
            self.assertEqual(committed, 2)
            self.assertEqual(ledger.total_invoice_count(), 2)

            backup = ledger.export_json(root / "backup.json")
            bdata = json.loads(backup.read_text(encoding="utf-8"))
            self.assertEqual(bdata["batch_count"], 1)
            self.assertEqual(bdata["invoice_count"], 2)

            recs2, summary2 = process_invoices([f1, f3], ledger=ledger)
            self.assertEqual(summary2.duplicate_history_count, 2)
            self.assertEqual(summary2.valid_total_amount, Decimal("0.00"))

            self.assertEqual(ledger.rollback_batch(bid), 2)
            recs3, summary3 = process_invoices([f1], ledger=ledger)
            self.assertEqual(summary3.parsed_ok, 1)
            self.assertEqual(recs3[0].status, "ok")

    def test_template_detection_fill_and_2up_pdf_merge(self):
        import pymupdf

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = InvoiceLedger(root / "ledger.db")
            make_einvoice_xml(root / "inv1.xml", invoice_number="26112000000444444441", excl="100.00", tax="6.00", total="106.00")
            make_vector_pdf_invoice(root / "inv2.pdf", invoice_number="26112000000444444442", excl="200.00", tax="12.00", total="212.00")
            make_vector_pdf_invoice(root / "inv3.pdf", invoice_number="26112000000444444443", excl="300.00", tax="18.00", total="318.00")
            make_vector_pdf_invoice(root / "inv4.pdf", invoice_number="26112000000444444444", excl="400.00", tax="24.00", total="424.00")

            recs, summary = process_invoices([root], ledger=ledger)
            xlsx = export_reimbursement_excel(recs, summary, root / "out.xlsx")
            wb = load_workbook(str(xlsx))
            self.assertEqual(wb.sheetnames, ["报销明细", "销方汇总", "异常与查重清单"])
            self.assertEqual(wb["报销明细"].cell(row=6, column=15).value, 1060.0)

            tpl_path = root / "company_tpl.xlsx"
            twb = Workbook()
            tws = twb.active
            tws.title = "费用报销单"
            tws["A1"] = "XX公司费用报销单"
            tws["A3"] = "开票日期"
            tws["B3"] = "发票号码"
            tws["C3"] = "销售方名称"
            tws["D3"] = "价税合计"
            twb.save(str(tpl_path))

            info = read_template_info(tpl_path)
            self.assertEqual(info["header_row"], 3)
            self.assertEqual(info["suggested_start_row"], 4)
            self.assertEqual(info["columns"]["B"], "发票号码")

            mapping = {
                "sheet": "费用报销单",
                "start_row": 4,
                "columns": {"issue_date": "A", "invoice_number": "B", "seller_name": "C", "total_amount": "D"},
            }
            filled_path, filled_count = fill_company_template(recs, tpl_path, mapping, root / "filled.xlsx")
            self.assertEqual(filled_count, 4)
            fwb = load_workbook(str(filled_path))
            self.assertEqual(fwb["费用报销单"]["D4"].value, 106.0)

            merged_pdf, page_count = merge_valid_record_pdfs(recs, root / "print_a4.pdf")
            self.assertEqual(page_count, 3)
            with pymupdf.open(str(merged_pdf)) as doc:
                self.assertEqual(len(doc), 2)

    def test_non_destructive_archive_renamer(self):
        self.assertEqual(sanitize_filename_part("CON"), "_CON")
        self.assertEqual(sanitize_filename_part('a<b>c:d/e\\f|g?h*i'), "a_b_c_d_e_f_g_h_i")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src_dir = root / "src"
            src_dir.mkdir()
            f1 = make_einvoice_xml(
                src_dir / "1.xml",
                invoice_number="26112000000100001111",
                seller_name="测试:公司/A",
                excl="100.00",
                tax="6.00",
                total="106.00",
            )
            f2 = make_einvoice_xml(
                src_dir / "2.xml",
                invoice_number="26112000000200001111",
                seller_name="测试:公司/A",
                excl="100.00",
                tax="6.00",
                total="106.00",
            )
            recs, _ = process_invoices([src_dir], ledger=InvoiceLedger(root / "l.db"))

            out_dir = root / "archived"
            map_path, items = archive_rename_invoices(recs, out_dir)
            self.assertTrue(map_path.is_file())
            self.assertEqual(len(items), 2)
            # 重名尾号 1111 自动加 _2，且源文件完好保留
            names = sorted(i["archived_name"] for i in items)
            self.assertEqual(names[0], "2026-11-18_106.00元_测试_公司_A_1111.xml")
            self.assertEqual(names[1], "2026-11-18_106.00元_测试_公司_A_1111_2.xml")
            self.assertTrue(f1.is_file() and f2.is_file())
            self.assertIn("2026-11-18", build_archive_filename(recs[0]))

    def test_cli_full_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            in_dir = root / "in"
            in_dir.mkdir()
            make_einvoice_xml(in_dir / "cli.xml")
            make_vector_pdf_invoice(in_dir / "cli.pdf", invoice_number="26112000000999888777")
            db = root / "l.db"

            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "piaosu",
                    "scan",
                    str(in_dir),
                    "--ledger",
                    str(db),
                    "--excel",
                    str(root / "out.xlsx"),
                    "--merge-pdf",
                    str(root / "out.pdf"),
                    "--archive-dir",
                    str(root / "arch"),
                    "--commit",
                    "--note",
                    "CLI集成测试",
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data["summary"]["parsed_ok"], 2)
            self.assertEqual(data["committed_batch"]["committed"], 2)
            self.assertEqual(data["archived"]["count"], 2)

            bid = data["committed_batch"]["batch_id"]
            exp_proc = subprocess.run(
                [sys.executable, "-m", "piaosu", "ledger", "export-json", str(root / "dump.json"), "--ledger", str(db)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(exp_proc.returncode, 0, exp_proc.stderr)
            self.assertTrue((root / "dump.json").is_file())

            rb_proc = subprocess.run(
                [sys.executable, "-m", "piaosu", "ledger", "rollback", bid, "--ledger", str(db)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(rb_proc.returncode, 0, rb_proc.stderr)


class GUISmokeTests(unittest.TestCase):
    def test_gui_edit_template_mapper_and_ledger_dialogs(self):
        if os.name != "nt" and not os.environ.get("DISPLAY"):
            self.skipTest("headless environment without DISPLAY")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad_xml = make_einvoice_xml(root / "fixme.xml", excl="100.00", tax="6.00", total="999.00")
            tpl_path = root / "tpl.xlsx"
            twb = Workbook()
            tws = twb.active
            tws.title = "报销单"
            tws.append(["开票日期", "发票号码", "销售方名称", "价税合计"])
            twb.save(str(tpl_path))

            app = PiaoSuApp(ledger_path=root / "gui.db")
            try:
                recs, summary = process_invoices([bad_xml], ledger=app.ledger)
                app._apply_results(recs, summary)
                app.update()
                self.assertEqual(app.summary.error_count, 1)

                # 1. 人工双击弹窗修正金额后自动重算状态变为 ok
                dlg = EditRecordDialog(app, app.records[0], on_save=lambda _r: app.revalidate_all())
                dlg.vars["total_amount"].set("106.00")
                dlg.save()
                app.update()
                self.assertEqual(app.summary.parsed_ok, 1)
                self.assertEqual(app.summary.error_count, 0)

                # 2. 可视化模板列映射窗口自动探测工作表与表头列
                mapper = TemplateMapperDialog(app, tpl_path, app.records, preset_dir=root / "presets")
                app.update()
                cfg = mapper.build_mapping_config()
                self.assertEqual(cfg["sheet"], "报销单")
                self.assertEqual(cfg["columns"]["invoice_number"], "B")
                mapper.destroy()

                # 3. 历史台账管理与无确认阻断的回滚逻辑
                app.ledger.commit_batch(app.records, note="GUI测试批次", batch_id="B-GUI-1")
                ldlg = LedgerDialog(app, app.ledger, on_changed=app.revalidate_all)
                app.update()
                self.assertIn("B-GUI-1", ldlg.tree.get_children())
                ldlg.tree.selection_set("B-GUI-1")
                ldlg.rollback_selected(confirm=False)
                self.assertEqual(app.ledger.total_invoice_count(), 0)
                ldlg.destroy()
            finally:
                app.destroy()


class ExampleAssetsAndLauncherTests(unittest.TestCase):
    def test_shipped_example_template_and_mapping_work(self):
        root_dir = Path(__file__).resolve().parents[1]
        tpl = root_dir / "examples" / "示例企业报销单模板.xlsx"
        mapping = root_dir / "examples" / "示例模板映射.json"
        self.assertTrue(tpl.is_file())
        self.assertTrue(mapping.is_file())

        info = read_template_info(tpl)
        self.assertEqual(info["sheet"], "费用报销单")
        self.assertEqual(info["header_row"], 4)
        self.assertEqual(info["suggested_start_row"], 5)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            make_einvoice_xml(tmp_dir / "sample.xml")
            recs, _ = process_invoices([tmp_dir], ledger=InvoiceLedger(tmp_dir / "l.db"))
            out_xlsx, count = fill_company_template(recs, tpl, mapping, tmp_dir / "filled.xlsx")
            self.assertEqual(count, 1)
            wb = load_workbook(str(out_xlsx))
            self.assertEqual(wb["费用报销单"]["E5"].value, 1060.0)
            self.assertEqual(wb["费用报销单"]["E51"].value, "=SUM(E5:E50)")

    def test_windows_launcher_source_and_manifest(self):
        root_dir = Path(__file__).resolve().parents[1]
        c_src = (root_dir / "windows" / "launcher.c").read_text(encoding="utf-8")
        rc_src = (root_dir / "windows" / "piaosu.rc").read_text(encoding="utf-8")
        manifest = (root_dir / "windows" / "piaosu.manifest").read_text(encoding="utf-8")
        self.assertIn("_snwprintf", c_src)
        self.assertNotIn("swprintf(", c_src)
        self.assertIn("VS_VERSION_INFO", rc_src)
        self.assertIn("asInvoker", manifest)
        self.assertNotIn("requireAdministrator", manifest)


class ValidatorEdgeTests(unittest.TestCase):
    def test_unusual_invoice_number_and_bad_tax_id_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            xml = make_einvoice_xml(
                Path(tmp) / "warn.xml",
                invoice_number="123456",
                seller_tax_id="INVALID_TAX",
                issue_date="",
            )
            rec = validate_invoice(parse_invoice_file(xml))
            self.assertEqual(rec.status, "warning")
            codes = {i.code for i in rec.issues}
            self.assertIn("unusual_invoice_number", codes)
            self.assertIn("invalid_seller_tax_id", codes)
            self.assertIn("missing_or_bad_date", codes)

    def test_manual_edit_recovers_failed_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "corrupt.pdf"
            bad.write_bytes(b"broken")
            rec = validate_invoice(parse_invoice_file(bad))
            self.assertEqual(rec.status, "error")

            rec.manually_edited = True
            rec.issues = []
            rec.invoice_number = "26112000000123456789"
            rec.issue_date = "2026-11-22"
            rec.seller_name = "人工补录测试有限公司"
            rec.amount_excl_tax = Decimal("100.00")
            rec.tax_amount = Decimal("6.00")
            rec.total_amount = Decimal("106.00")
            validate_invoice(rec)
            self.assertEqual(rec.status, "ok")

    def test_unusual_18_digit_invoice_number_not_truncated_to_12(self):
        from piaosu.models import InvoiceRecord
        from piaosu.parsers.rules import extract_from_text

        rec = InvoiceRecord("a.pdf", "a.pdf", "hash", "pdf_vector")
        extract_from_text("电子发票（普通发票）\n发票号码：261120000004000009\n价税合计 ¥100.00", rec)
        self.assertEqual(rec.invoice_number, "261120000004000009")


if __name__ == "__main__":
    unittest.main()
