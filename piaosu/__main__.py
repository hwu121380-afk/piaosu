"""票溯（PiaoSu）命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from piaosu import PRODUCT_EN, PRODUCT_NAME, PRODUCT_VERSION
from piaosu.exporter import export_reimbursement_excel, fill_company_template
from piaosu.ledger import InvoiceLedger
from piaosu.merger import merge_valid_record_pdfs
from piaosu.parsers.ocr_fallback import is_ocr_available
from piaosu.renamer import archive_rename_invoices
from piaosu.service import process_invoices


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        enc = (getattr(stream, "encoding", None) or "").lower()
        try:
            if not stream.isatty() or enc in {"cp1252", "ascii", "ansi_x3.4-1968", ""}:
                reconfigure(encoding="utf-8", errors="replace")
            else:
                reconfigure(errors="replace")
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(
        prog="piaosu",
        description=f"{PRODUCT_NAME} ({PRODUCT_EN}) {PRODUCT_VERSION} — 纯离线数电票与传统票据报销引擎",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("gui", help="启动中文桌面图形界面")
    sub.add_parser("doctor", help="检查本地解析通道与可选 OCR 环境")

    scan = sub.add_parser("scan", help="批量扫描解析发票并执行财务校验与跨月查重")
    scan.add_argument("inputs", nargs="+", help="发票文件或文件夹路径")
    scan.add_argument("--ledger", default=None, help="自定义 SQLite 查重台账路径")
    scan.add_argument("--excel", default=None, help="导出报销汇总 Excel 路径")
    scan.add_argument("--merge-pdf", default=None, help="导出 A4 二合一合并打印 PDF 路径")
    scan.add_argument("--archive-dir", default=None, help="按规范命名复制归档到指定文件夹（不修改源文件）")
    scan.add_argument("--template", default=None, help="企业自定义 Excel 报销模板路径")
    scan.add_argument("--mapping", default=None, help="企业模板字段映射 JSON 路径")
    scan.add_argument("--template-out", default=None, help="填充后的企业模板输出路径")
    scan.add_argument("--commit", action="store_true", help="将本批有效发票写入历史查重台账")
    scan.add_argument("--note", default="", help="本次入账批次备注（如：2026-11差旅报销）")
    scan.add_argument("--json", action="store_true", dest="as_json", help="以 JSON 格式输出明细")
    scan.add_argument("--workers", type=int, default=4, help="并发线程数")

    ledger_cmd = sub.add_parser("ledger", help="管理本地历史查重台账")
    ledger_sub = ledger_cmd.add_subparsers(dest="ledger_action")
    l_list = ledger_sub.add_parser("list", help="查看历史入账批次")
    l_list.add_argument("--ledger", default=None, help="自定义 SQLite 台账路径")
    l_rb = ledger_sub.add_parser("rollback", help="撤销指定历史批次")
    l_rb.add_argument("batch_id", help="要撤销的批次号")
    l_rb.add_argument("--ledger", default=None, help="自定义 SQLite 台账路径")
    l_exp = ledger_sub.add_parser("export-json", help="将历史台账导出为 JSON 备份")
    l_exp.add_argument("dest", help="输出 JSON 文件路径")
    l_exp.add_argument("--ledger", default=None, help="自定义 SQLite 台账路径")

    args = parser.parse_args(argv)
    cmd = args.command or "gui"

    if cmd == "gui":
        from piaosu.gui import run_gui

        run_gui()
        return 0
    if cmd == "doctor":
        return _doctor()
    if cmd == "scan":
        return _scan(args)
    if cmd == "ledger":
        return _ledger(args)
    parser.print_help()
    return 2


def _doctor() -> int:
    print(f"{PRODUCT_NAME} ({PRODUCT_EN}) {PRODUCT_VERSION}")
    print("解析通道自检：")
    print("  - 数电票 XML 原生解析：就绪（标准库 + XXE 防护，0ms）")
    print("  - 国标 OFD 原生解析：就绪（标准库 + 坐标行重组 + ZipSlip 防护）")
    print("  - PDF 矢量文本提取与 A4 拼版合并：就绪（PyMuPDF / pypdf）")
    print(f"  - 本地离线 OCR（扫描件/图片）：{'就绪（rapidocr-onnxruntime）' if is_ocr_available() else '未安装（可选，pip install rapidocr-onnxruntime）'}")
    return 0


def _scan(args: argparse.Namespace) -> int:
    ledger = InvoiceLedger(Path(args.ledger)) if args.ledger else InvoiceLedger()
    records, summary = process_invoices(
        [Path(p) for p in args.inputs],
        ledger=ledger,
        max_workers=max(1, args.workers),
    )

    if args.excel:
        export_reimbursement_excel(records, summary, Path(args.excel))
    if args.template and args.mapping and args.template_out:
        fill_company_template(records, Path(args.template), Path(args.mapping), Path(args.template_out))
    if args.merge_pdf:
        merge_valid_record_pdfs(records, Path(args.merge_pdf))

    archived_info = None
    if args.archive_dir:
        map_path, archived_items = archive_rename_invoices(records, Path(args.archive_dir))
        archived_info = {"manifest": str(map_path), "count": len(archived_items)}

    batch_info = None
    if args.commit:
        bid, count = ledger.commit_batch(records, note=args.note)
        batch_info = {"batch_id": bid, "committed": count}

    if args.as_json:
        payload = {
            "summary": {
                "total_files": summary.total_files,
                "parsed_ok": summary.parsed_ok,
                "warning_count": summary.warning_count,
                "duplicate_batch_count": summary.duplicate_batch_count,
                "duplicate_history_count": summary.duplicate_history_count,
                "error_count": summary.error_count,
                "valid_total_amount": f"{summary.valid_total_amount:.2f}",
                "valid_tax_amount": f"{summary.valid_tax_amount:.2f}",
                "elapsed_ms": summary.elapsed_ms,
                "channel_counts": summary.channel_counts,
            },
            "committed_batch": batch_info,
            "archived": archived_info,
            "records": [r.to_dict() for r in records],
        }
        use_ascii = (getattr(sys.stdout, "encoding", None) or "").lower().replace("-", "") != "utf8"
        print(json.dumps(payload, ensure_ascii=use_ascii, indent=2))
        return 0

    print(
        f"共处理 {summary.total_files} 个文件（耗时 {summary.elapsed_ms:.1f} ms） | "
        f"正常 {summary.parsed_ok} | 待复核 {summary.warning_count} | "
        f"批内重复 {summary.duplicate_batch_count} | 历史重复 {summary.duplicate_history_count} | 异常 {summary.error_count}"
    )
    print(f"有效价税合计：¥{summary.valid_total_amount:.2f}（其中税额 ¥{summary.valid_tax_amount:.2f}）")
    for r in records:
        amt = f"¥{r.total_amount:.2f}" if r.total_amount is not None else "—"
        print(f"  [{r.status}] ({r.parse_channel}) {r.file_name} | 票号:{r.invoice_number or '—'} | {r.issue_date or '—'} | {amt} | {r.seller_name or '—'}")
    if args.excel:
        print(f"已导出报销台账：{args.excel}")
    if args.merge_pdf:
        print(f"已导出 A4 二合一打印 PDF：{args.merge_pdf}")
    if archived_info:
        print(f"已安全复制归档 {archived_info['count']} 张发票，对照表：{archived_info['manifest']}")
    if batch_info:
        print(f"已写入历史查重台账：批次 {batch_info['batch_id']}（入账 {batch_info['committed']} 张）")
    return 0


def _ledger(args: argparse.Namespace) -> int:
    ledger = InvoiceLedger(Path(args.ledger)) if getattr(args, "ledger", None) else InvoiceLedger()
    if args.ledger_action == "list":
        batches = ledger.list_batches()
        if not batches:
            print("历史台账为空。")
            return 0
        for b in batches:
            print(f"{b['batch_id']}  |  {b['created_at']}  |  {b['record_count']} 张  |  ¥{b['total_amount']}  |  {b['note']}")
        return 0
    if args.ledger_action == "rollback":
        deleted = ledger.rollback_batch(args.batch_id)
        print(f"已撤销批次 {args.batch_id}，移除 {deleted} 条历史发票记录。")
        return 0
    if args.ledger_action == "export-json":
        dest = ledger.export_json(Path(args.dest))
        print(f"已导出历史台账 JSON 备份：{dest}")
        return 0
    print("请指定 ledger 子命令：list、rollback 或 export-json")
    return 2


if __name__ == "__main__":
    sys.exit(main())
