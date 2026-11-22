"""票溯（PiaoSu）中文离线桌面端（Tkinter/ttk）：支持人工双击纠错、可视化企业模板映射、台账回滚与安全归档。"""

from __future__ import annotations

import json
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable

from piaosu import PRODUCT_EN, PRODUCT_NAME, PRODUCT_VERSION
from piaosu.exporter import export_reimbursement_excel, fill_company_template, read_template_info
from piaosu.ledger import InvoiceLedger
from piaosu.merger import merge_valid_record_pdfs
from piaosu.models import BatchSummary, InvoiceRecord, to_decimal
from piaosu.parsers.ocr_fallback import is_ocr_available
from piaosu.renamer import archive_rename_invoices
from piaosu.service import process_invoices, summarize_batch
from piaosu.validator import validate_invoice

BG = "#f5f7fa"
PANEL = "#ffffff"
NAVY = "#1b3044"
INK = "#1d2430"
MUTED = "#5c6570"

_STATUS_CN = {
    "ok": "正常",
    "warning": "待复核",
    "duplicate_batch": "批内重复",
    "duplicate_history": "历史重复",
    "error": "异常",
}

_TEMPLATE_FIELDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("issue_date", "开票日期", ("日期", "时间")),
    ("invoice_number", "发票号码", ("发票号", "票号", "号码")),
    ("seller_name", "销售方名称", ("销售方", "销方", "收款方", "商户")),
    ("item_summary", "项目/费用摘要", ("摘要", "项目", "品名", "内容", "事由")),
    ("amount_excl_tax", "不含税金额", ("不含税",)),
    ("tax_amount", "税额", ("税额",)),
    ("total_amount", "价税合计（总金额）", ("价税合计", "合计", "总金额", "金额")),
    ("buyer_name", "购买方名称", ("购买方", "购方", "抬头")),
    ("remark", "备注", ("备注", "说明")),
)


class EditRecordDialog(tk.Toplevel):
    """双击发票行后弹出的核对与人工修正窗口，保存后自动重跑财务勾稽与全批查重。"""

    def __init__(
        self,
        parent: tk.Misc,
        record: InvoiceRecord,
        on_save: Callable[[InvoiceRecord], None],
    ) -> None:
        super().__init__(parent)
        self.record = record
        self.on_save = on_save
        self.title(f"核对与修正 — {record.file_name}")
        self.geometry("620x620")
        self.configure(bg=BG)
        self.transient(parent)

        issue_lines = []
        if record.duplicate_ref:
            issue_lines.append(f"[重复] {record.duplicate_ref}")
        for iss in record.issues:
            issue_lines.append(f"[{iss.severity}] {iss.message}")
        issue_text = "\n".join(issue_lines) if issue_lines else "当前无校验异常，可核对或补充字段。"

        banner = tk.Label(
            self,
            text=issue_text,
            bg="#fff4e5" if issue_lines else "#edf7ed",
            fg=INK,
            font=("Microsoft YaHei UI", 9),
            justify="left",
            anchor="w",
            padx=12,
            pady=8,
            wraplength=580,
        )
        banner.pack(fill="x", padx=14, pady=(12, 8))

        form = tk.Frame(self, bg=PANEL, padx=14, pady=10, highlightthickness=1, highlightbackground="#e2e8f0")
        form.pack(fill="both", expand=True, padx=14)

        self.vars: dict[str, tk.StringVar] = {}
        fields = [
            ("invoice_number", "发票号码"),
            ("invoice_code", "发票代码"),
            ("issue_date", "开票日期 (YYYY-MM-DD)"),
            ("seller_name", "销售方名称"),
            ("seller_tax_id", "销售方税号"),
            ("buyer_name", "购买方名称"),
            ("buyer_tax_id", "购买方税号"),
            ("amount_excl_tax", "不含税金额"),
            ("tax_amount", "税额"),
            ("total_amount", "价税合计"),
            ("tax_rate", "税率"),
            ("item_summary", "项目/品名摘要"),
            ("remark", "备注"),
        ]
        for row_idx, (key, label) in enumerate(fields):
            tk.Label(form, text=label, bg=PANEL, fg=INK, font=("Microsoft YaHei UI", 9), anchor="w").grid(
                row=row_idx, column=0, sticky="w", pady=4, padx=(0, 12)
            )
            raw_val = getattr(record, key)
            text_val = f"{raw_val:.2f}" if key in ("amount_excl_tax", "tax_amount", "total_amount") and raw_val is not None else (raw_val or "")
            var = tk.StringVar(value=str(text_val))
            self.vars[key] = var
            ttk.Entry(form, textvariable=var, width=48).grid(row=row_idx, column=1, sticky="ew", pady=4)
        form.columnconfigure(1, weight=1)

        btns = tk.Frame(self, bg=BG, pady=10, padx=14)
        btns.pack(fill="x")
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="保存并重新校验", command=self.save).pack(side="right", padx=(0, 8))

    def save(self) -> None:
        self.record.invoice_number = self.vars["invoice_number"].get().strip()
        self.record.invoice_code = self.vars["invoice_code"].get().strip()
        self.record.issue_date = self.vars["issue_date"].get().strip()
        self.record.seller_name = self.vars["seller_name"].get().strip()
        self.record.seller_tax_id = self.vars["seller_tax_id"].get().strip()
        self.record.buyer_name = self.vars["buyer_name"].get().strip()
        self.record.buyer_tax_id = self.vars["buyer_tax_id"].get().strip()
        self.record.amount_excl_tax = to_decimal(self.vars["amount_excl_tax"].get())
        self.record.tax_amount = to_decimal(self.vars["tax_amount"].get())
        self.record.total_amount = to_decimal(self.vars["total_amount"].get())
        self.record.tax_rate = self.vars["tax_rate"].get().strip()
        self.record.item_summary = self.vars["item_summary"].get().strip()
        self.record.remark = self.vars["remark"].get().strip()

        self.record.manually_edited = True
        self.record.issues = []
        self.record.duplicate_ref = ""
        validate_invoice(self.record)
        self.on_save(self.record)
        self.destroy()


class TemplateMapperDialog(tk.Toplevel):
    """可视化企业 Excel 报销单模板映射窗口（免手写 JSON，自动探测表头列并支持保存预设）。"""

    def __init__(
        self,
        parent: tk.Misc,
        template_path: Path,
        records: list[InvoiceRecord],
        *,
        preset_dir: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.template_path = template_path
        self.records = records
        self.preset_dir = preset_dir or (Path.home() / ".piaosu" / "templates")
        self.title(f"企业模板字段映射 — {template_path.name}")
        self.geometry("680x580")
        self.configure(bg=BG)
        self.transient(parent)

        self.info = read_template_info(template_path)
        self.sheet_var = tk.StringVar(value=self.info["sheet"])
        self.header_row_var = tk.IntVar(value=self.info["header_row"])
        self.start_row_var = tk.IntVar(value=self.info["suggested_start_row"])
        self.field_combos: dict[str, ttk.Combobox] = {}
        self.field_vars: dict[str, tk.StringVar] = {}
        self._build_ui()
        self.refresh_columns(auto_guess=True)

    def _build_ui(self) -> None:
        top = tk.Frame(self, bg=PANEL, padx=14, pady=10, highlightthickness=1, highlightbackground="#e2e8f0")
        top.pack(fill="x", padx=14, pady=(12, 8))

        tk.Label(top, text="工作表：", bg=PANEL, font=("Microsoft YaHei UI", 9)).grid(row=0, column=0, sticky="w")
        sheet_box = ttk.Combobox(top, textvariable=self.sheet_var, values=self.info["sheets"], state="readonly", width=20)
        sheet_box.grid(row=0, column=1, padx=(4, 14), sticky="w")
        sheet_box.bind("<<ComboboxSelected>>", lambda _e: self.on_sheet_or_header_change())

        tk.Label(top, text="表头行：", bg=PANEL, font=("Microsoft YaHei UI", 9)).grid(row=0, column=2, sticky="w")
        ttk.Spinbox(top, from_=1, to=30, textvariable=self.header_row_var, width=5).grid(row=0, column=3, padx=(4, 12))

        tk.Label(top, text="数据起始行：", bg=PANEL, font=("Microsoft YaHei UI", 9)).grid(row=0, column=4, sticky="w")
        ttk.Spinbox(top, from_=2, to=50, textvariable=self.start_row_var, width=5).grid(row=0, column=5, padx=(4, 10))

        ttk.Button(top, text="读取表头", command=self.on_sheet_or_header_change).grid(row=0, column=6)

        mid = tk.Frame(self, bg=PANEL, padx=14, pady=10, highlightthickness=1, highlightbackground="#e2e8f0")
        mid.pack(fill="both", expand=True, padx=14)
        tk.Label(mid, text="发票字段", bg=PANEL, font=("Microsoft YaHei UI", 9, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 6))
        tk.Label(mid, text="写入模板列", bg=PANEL, font=("Microsoft YaHei UI", 9, "bold")).grid(row=0, column=1, sticky="w", pady=(0, 6))

        for row_idx, (key, label, _hints) in enumerate(_TEMPLATE_FIELDS, start=1):
            tk.Label(mid, text=f"{label} ({key})", bg=PANEL, font=("Microsoft YaHei UI", 9)).grid(
                row=row_idx, column=0, sticky="w", pady=4, padx=(0, 16)
            )
            var = tk.StringVar(value="(不映射)")
            combo = ttk.Combobox(mid, textvariable=var, state="readonly", width=36)
            combo.grid(row=row_idx, column=1, sticky="ew", pady=4)
            self.field_vars[key] = var
            self.field_combos[key] = combo
        mid.columnconfigure(1, weight=1)

        bottom = tk.Frame(self, bg=BG, padx=14, pady=10)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="保存预设", command=self.save_preset).pack(side="left")
        ttk.Button(bottom, text="加载预设", command=self.load_preset).pack(side="left", padx=8)
        ttk.Button(bottom, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(bottom, text="生成填好后的报销单", command=self.export_filled).pack(side="right", padx=(0, 8))

    def on_sheet_or_header_change(self) -> None:
        self.info = read_template_info(
            self.template_path,
            sheet_name=self.sheet_var.get(),
            header_row=int(self.header_row_var.get()),
        )
        self.start_row_var.set(self.info["suggested_start_row"])
        self.refresh_columns(auto_guess=True)

    def refresh_columns(self, auto_guess: bool = False) -> None:
        cols: dict[str, str] = self.info.get("columns", {})
        options = ["(不映射)"] + [f"{col} ({name})" for col, name in cols.items()]
        for key, _label, hints in _TEMPLATE_FIELDS:
            combo = self.field_combos[key]
            combo["values"] = options
            if auto_guess:
                matched = "(不映射)"
                for col, name in cols.items():
                    if any(h in name for h in hints):
                        matched = f"{col} ({name})"
                        break
                self.field_vars[key].set(matched)

    def build_mapping_config(self) -> dict:
        columns: dict[str, str] = {}
        for key, var in self.field_vars.items():
            choice = var.get().strip()
            if choice and choice != "(不映射)":
                columns[key] = choice.split(" ", 1)[0]
        return {
            "sheet": self.sheet_var.get(),
            "start_row": int(self.start_row_var.get()),
            "columns": columns,
        }

    def save_preset(self) -> None:
        self.preset_dir.mkdir(parents=True, exist_ok=True)
        dest = filedialog.asksaveasfilename(
            parent=self,
            title="保存模板映射预设",
            initialdir=str(self.preset_dir),
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if not dest:
            return
        Path(dest).write_text(json.dumps(self.build_mapping_config(), ensure_ascii=False, indent=2), encoding="utf-8")
        messagebox.showinfo("已保存", f"映射预设已保存：\n{dest}", parent=self)

    def load_preset(self) -> None:
        self.preset_dir.mkdir(parents=True, exist_ok=True)
        src = filedialog.askopenfilename(
            parent=self,
            title="加载模板映射预设",
            initialdir=str(self.preset_dir),
            filetypes=[("JSON", "*.json")],
        )
        if not src:
            return
        cfg = json.loads(Path(src).read_text(encoding="utf-8"))
        if cfg.get("sheet") in self.info["sheets"]:
            self.sheet_var.set(cfg["sheet"])
            self.on_sheet_or_header_change()
        if cfg.get("start_row"):
            self.start_row_var.set(int(cfg["start_row"]))
        col_map = cfg.get("columns") or {}
        cols = self.info.get("columns", {})
        for key, var in self.field_vars.items():
            letter = col_map.get(key)
            if letter:
                var.set(f"{letter} ({cols[letter]})" if letter in cols else letter)
            else:
                var.set("(不映射)")

    def export_filled(self) -> None:
        cfg = self.build_mapping_config()
        if not cfg["columns"]:
            messagebox.showwarning("未映射字段", "请至少选择一个要写入的列。", parent=self)
            return
        dest = filedialog.asksaveasfilename(
            parent=self,
            title="保存填充后的报销单",
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")],
        )
        if not dest:
            return
        _, count = fill_company_template(self.records, self.template_path, cfg, Path(dest))
        messagebox.showinfo("填表完成", f"已按企业模板填充 {count} 条有效发票：\n{dest}", parent=self)
        self.destroy()


class LedgerDialog(tk.Toplevel):
    """历史查重台账管理窗口：查看入账批次、撤销回滚与导出 JSON 备份。"""

    def __init__(
        self,
        parent: tk.Misc,
        ledger: InvoiceLedger,
        on_changed: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self.ledger = ledger
        self.on_changed = on_changed
        self.title("历史查重台账管理")
        self.geometry("760x440")
        self.configure(bg=BG)
        self.transient(parent)

        self.info_var = tk.StringVar()
        tk.Label(self, textvariable=self.info_var, bg=BG, fg=INK, font=("Microsoft YaHei UI", 10, "bold"), anchor="w").pack(
            fill="x", padx=14, pady=(12, 6)
        )

        frame = tk.Frame(self, bg=BG, padx=14)
        frame.pack(fill="both", expand=True)
        cols = ("batch_id", "created_at", "count", "total", "note")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        for col, text, w in (
            ("batch_id", "批次号", 160),
            ("created_at", "入账时间 (UTC)", 160),
            ("count", "发票张数", 80),
            ("total", "有效总额", 110),
            ("note", "批次备注", 210),
        ):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w, anchor="e" if col in ("count", "total") else "w")
        self.tree.pack(side="left", fill="both", expand=True)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")

        btns = tk.Frame(self, bg=BG, padx=14, pady=10)
        btns.pack(fill="x")
        ttk.Button(btns, text="撤销选中批次（回滚）", command=self.rollback_selected).pack(side="left")
        ttk.Button(btns, text="导出台账 JSON 备份", command=self.export_backup).pack(side="left", padx=8)
        ttk.Button(btns, text="关闭", command=self.destroy).pack(side="right")

        self.refresh_batches()

    def refresh_batches(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        batches = self.ledger.list_batches(limit=100)
        total_inv = self.ledger.total_invoice_count()
        self.info_var.set(f"当前台账路径：{self.ledger.db_path}  |  累计已入账 {total_inv} 张发票（{len(batches)} 个批次）")
        for b in batches:
            self.tree.insert(
                "",
                "end",
                iid=b["batch_id"],
                values=(b["batch_id"], b["created_at"], b["record_count"], f"¥{b['total_amount']}", b["note"]),
            )

    def rollback_selected(self, *, confirm: bool = True) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选中一个历史批次。", parent=self)
            return
        bid = sel[0]
        if confirm and not messagebox.askyesno("确认撤销", f"确定要撤销批次 {bid} 吗？撤销后该批发票可重新入账。", parent=self):
            return
        deleted = self.ledger.rollback_batch(bid)
        self.refresh_batches()
        self.on_changed()
        if confirm:
            messagebox.showinfo("撤销完成", f"已移除批次 {bid} 的 {deleted} 条历史记录。", parent=self)

    def export_backup(self) -> None:
        dest = filedialog.asksaveasfilename(
            parent=self,
            title="导出历史台账 JSON 备份",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if not dest:
            return
        self.ledger.export_json(Path(dest))
        messagebox.showinfo("备份完成", f"台账已备份至：\n{dest}", parent=self)


class PiaoSuApp(tk.Tk):
    def __init__(self, ledger_path: Path | None = None) -> None:
        super().__init__()
        self.title(f"{PRODUCT_NAME} ({PRODUCT_EN}) {PRODUCT_VERSION} — 纯离线数电票报销引擎")
        self.geometry("1220x780")
        self.minsize(980, 640)
        self.configure(bg=BG)

        self.ledger = InvoiceLedger(ledger_path)
        self.records: list[InvoiceRecord] = []
        self.summary: BatchSummary = BatchSummary()
        self._build_ui()

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(10, 6))
        style.configure("Treeview", font=("Microsoft YaHei UI", 10), rowheight=26)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))

        header = tk.Frame(self, bg=NAVY, padx=16, pady=12)
        header.pack(fill="x")
        tk.Label(
            header,
            text=f"{PRODUCT_NAME} {PRODUCT_VERSION}",
            bg=NAVY,
            fg="#ffffff",
            font=("Microsoft YaHei UI", 16, "bold"),
        ).pack(side="left")
        ocr_badge = "本地 OCR：已就绪" if is_ocr_available() else "本地 OCR：未安装（XML/OFD/PDF 矢量直提可用）"
        tk.Label(
            header,
            text=f"纯本地离线 · 零数据上传 · 双击表格行可人工修正 · {ocr_badge}",
            bg=NAVY,
            fg="#cbd5e1",
            font=("Microsoft YaHei UI", 10),
        ).pack(side="right")

        toolbar = tk.Frame(self, bg=BG, padx=16, pady=10)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="选择发票文件夹", command=self.pick_folder).pack(side="left")
        ttk.Button(toolbar, text="选择多个发票文件", command=self.pick_files).pack(side="left", padx=8)
        ttk.Button(toolbar, text="清空列表", command=self.clear_results).pack(side="left")
        ttk.Button(toolbar, text="历史查重台账管理", command=self.show_ledger_dialog).pack(side="right")

        self.kpi_var = tk.StringVar(value="请选择包含数电票 XML / OFD / PDF / 图片的文件夹开始离线解析与查重。")
        kpi_bar = tk.Frame(self, bg=PANEL, highlightthickness=1, highlightbackground="#e2e8f0", padx=14, pady=10)
        kpi_bar.pack(fill="x", padx=16, pady=(0, 10))
        tk.Label(
            kpi_bar,
            textvariable=self.kpi_var,
            bg=PANEL,
            fg=INK,
            font=("Microsoft YaHei UI", 10, "bold"),
            anchor="w",
        ).pack(fill="x")

        table_frame = tk.Frame(self, bg=BG, padx=16)
        table_frame.pack(fill="both", expand=True)
        cols = ("status", "channel", "number", "date", "seller", "total", "tax", "note", "file")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="browse")
        headers = {
            "status": ("状态", 80),
            "channel": ("解析通道", 90),
            "number": ("发票号码", 170),
            "date": ("开票日期", 95),
            "seller": ("销售方名称", 200),
            "total": ("价税合计", 95),
            "tax": ("税额", 80),
            "note": ("查重/校验说明", 230),
            "file": ("文件名", 150),
        }
        for key, (label, width) in headers.items():
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="e" if key in ("total", "tax") else "w")

        self.tree.tag_configure("ok", background="#ffffff")
        self.tree.tag_configure("warning", background="#fff4e5")
        self.tree.tag_configure("duplicate_batch", background="#fdecea")
        self.tree.tag_configure("duplicate_history", background="#fdecea")
        self.tree.tag_configure("error", background="#fdecea")
        self.tree.bind("<Double-1>", self._on_row_double_click)

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        footer = tk.Frame(self, bg=BG, padx=16, pady=12)
        footer.pack(fill="x")
        ttk.Button(footer, text="导出报销汇总 Excel", command=self.export_excel).pack(side="left")
        ttk.Button(footer, text="按企业模板填表", command=self.export_template).pack(side="left", padx=8)
        ttk.Button(footer, text="生成 A4 二合一打印 PDF", command=self.export_merged_pdf).pack(side="left")
        ttk.Button(footer, text="规范化归档重命名", command=self.archive_rename).pack(side="left", padx=8)
        ttk.Button(footer, text="写入历史查重台账", command=self.commit_to_ledger).pack(side="right")

    def pick_folder(self) -> None:
        folder = filedialog.askdirectory(title="选择发票文件夹")
        if folder:
            self.load_inputs([Path(folder)])

    def pick_files(self) -> None:
        files = filedialog.askopenfilenames(
            title="选择发票文件",
            filetypes=[("发票文件", "*.xml *.ofd *.pdf *.jpg *.jpeg *.png *.bmp"), ("所有文件", "*.*")],
        )
        if files:
            self.load_inputs([Path(f) for f in files])

    def load_inputs(self, paths: list[Path]) -> None:
        self.kpi_var.set("正在并发解析与跨月查重，请稍候…")
        self.update_idletasks()

        def worker() -> None:
            records, summary = process_invoices(paths, ledger=self.ledger)
            self.after(0, lambda: self._apply_results(records, summary))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_results(self, records: list[InvoiceRecord], summary: BatchSummary) -> None:
        self.records = records
        self.summary = summary
        for item in self.tree.get_children():
            self.tree.delete(item)
        for idx, rec in enumerate(records):
            note = rec.duplicate_ref or "；".join(i.message for i in rec.issues) or rec.item_summary
            self.tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    _STATUS_CN.get(rec.status, rec.status),
                    rec.parse_channel,
                    rec.invoice_number or "—",
                    rec.issue_date or "—",
                    rec.seller_name or "—",
                    f"{rec.total_amount:.2f}" if rec.total_amount is not None else "—",
                    f"{rec.tax_amount:.2f}" if rec.tax_amount is not None else "—",
                    note,
                    rec.file_name,
                ),
                tags=(rec.status,),
            )
        self.kpi_var.set(
            f"共 {summary.total_files} 份（{summary.elapsed_ms:.1f} ms） | "
            f"正常 {summary.parsed_ok} · 待复核 {summary.warning_count} · "
            f"批内重复 {summary.duplicate_batch_count} · 历史重复 {summary.duplicate_history_count} · 异常 {summary.error_count} | "
            f"有效价税合计：¥{summary.valid_total_amount:.2f}（税额 ¥{summary.valid_tax_amount:.2f}）"
        )

    def revalidate_all(self) -> None:
        if not self.records:
            return
        self.ledger.mark_duplicates(self.records)
        summary = summarize_batch(self.records, self.summary.elapsed_ms)
        self._apply_results(self.records, summary)

    def _on_row_double_click(self, _event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if 0 <= idx < len(self.records):
            EditRecordDialog(self, self.records[idx], on_save=lambda _rec: self.revalidate_all())

    def clear_results(self) -> None:
        self.records = []
        self.summary = BatchSummary()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.kpi_var.set("列表已清空。")

    def export_excel(self) -> None:
        if not self.records:
            messagebox.showinfo("提示", "请先加载发票。")
            return
        dest = filedialog.asksaveasfilename(title="保存报销汇总 Excel", defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")])
        if not dest:
            return
        export_reimbursement_excel(self.records, self.summary, Path(dest))
        messagebox.showinfo("导出成功", f"已导出报销台账：\n{dest}")

    def export_template(self) -> None:
        if not self.records:
            messagebox.showinfo("提示", "请先加载发票。")
            return
        tpl = filedialog.askopenfilename(title="选择企业 Excel 报销模板", filetypes=[("Excel", "*.xlsx")])
        if not tpl:
            return
        TemplateMapperDialog(self, Path(tpl), self.records)

    def export_merged_pdf(self) -> None:
        if not self.records:
            messagebox.showinfo("提示", "请先加载发票。")
            return
        dest = filedialog.asksaveasfilename(title="保存 A4 二合一打印 PDF", defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if not dest:
            return
        try:
            _, pages = merge_valid_record_pdfs(self.records, Path(dest))
        except ValueError as exc:
            messagebox.showwarning("无法合并", str(exc))
            return
        messagebox.showinfo("合并成功", f"已合并 {pages} 页有效 PDF 发票为 A4 二合一打印文件：\n{dest}")

    def archive_rename(self) -> None:
        if not self.records:
            messagebox.showinfo("提示", "请先加载发票。")
            return
        dest_dir = filedialog.askdirectory(title="选择规范化归档输出文件夹（不会修改原文件）")
        if not dest_dir:
            return
        map_path, items = archive_rename_invoices(self.records, Path(dest_dir))
        messagebox.showinfo("归档完成", f"已安全复制归档 {len(items)} 张有效发票（源文件未改动）：\n{map_path}")

    def commit_to_ledger(self) -> None:
        if not self.records:
            messagebox.showinfo("提示", "没有可入账的发票。")
            return
        note = simpledialog.askstring("写入历史查重台账", "请输入本次报销批次备注（如：2026-11差旅报销）：", parent=self)
        if note is None:
            return
        bid, count = self.ledger.commit_batch(self.records, note=note.strip())
        messagebox.showinfo("入账完成", f"已写入台账批次 {bid}，共入账 {count} 张有效发票。\n下次扫描相同发票将自动标红拦截。")

    def show_ledger_dialog(self) -> None:
        LedgerDialog(self, self.ledger, on_changed=self.revalidate_all)


def run_gui() -> None:
    app = PiaoSuApp()
    app.mainloop()
