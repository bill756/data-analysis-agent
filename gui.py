"""Tkinter GUI for the safe read-only data analysis agent.

Reuses the existing pipeline (loaders / DataAnalysisAgent / OpenAIAnalyst).
The blocking analysis runs in a background thread; results come back through a
queue polled with root.after(), so the UI never freezes.

Usage: python gui.py
"""

from __future__ import annotations

import json
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core import AnalysisResult, DataAnalysisAgent, connect_read_only
from loaders import load_into_memory
from openai_model import OpenAIAnalyst

MATPLOTLIB_OK = True
try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
except ImportError:
    MATPLOTLIB_OK = False

SUPPORTED_EXTENSIONS = (".db", ".txt", ".csv", ".xlsx", ".xls", ".json")
FILE_TYPES = [("数据文件", "*.db *.txt *.csv *.xlsx *.xls *.json"), ("所有文件", "*.*")]
ENV_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")


def run_analysis(files: list[Path], question: str, model: OpenAIAnalyst) -> AnalysisResult:
    """Pure logic (GUI-independent): load data and run the agent. Raises ValueError for bad input."""
    if not files:
        raise ValueError("请先选择数据文件")
    if not question.strip():
        raise ValueError("请输入问题")
    db_files = [f for f in files if f.suffix.lower() == ".db"]
    other_files = [f for f in files if f.suffix.lower() != ".db"]
    if db_files and other_files:
        raise ValueError("不支持同时使用 .db 与其他格式文件，请分开分析")
    if len(db_files) > 1:
        raise ValueError("一次只能分析一个 .db 文件")
    connection = connect_read_only(db_files[0]) if db_files else load_into_memory(files)
    try:
        return DataAnalysisAgent(connection, model=model).run(question)
    finally:
        connection.close()


def write_env_file(path: Path, updates: dict[str, str]) -> None:
    """Update KEY=VALUE entries in a .env file, preserving comments and other lines."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out: list[str] = []
    written: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key.startswith("export "):
                key = key[7:].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                written.add(key)
                continue
        out.append(line)
    for key, value in updates.items():
        if key not in written:
            out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


class AnalysisGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("数据分析Agent")
        root.geometry("880x660")
        root.minsize(720, 520)

        self.files: list[Path] = []
        self.result: AnalysisResult | None = None
        self.queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.model: OpenAIAnalyst | None = None
        self._poll_job: str | None = None
        self._try_create_model()

        root.protocol("WM_DELETE_WINDOW", self.destroy)
        self._build_ui()
        self._poll_queue()

    # ---------- model ----------

    def _try_create_model(self) -> None:
        try:
            self.model = OpenAIAnalyst()
        except ValueError as exc:
            self.model = None
            self._config_error = str(exc)
        else:
            self._config_error = ""

    def _refresh_model(self, api_key: str, base_url: str, model: str) -> bool:
        try:
            self.model = OpenAIAnalyst(
                api_key=api_key or None, base_url=base_url or None, model=model or None
            )
        except ValueError as exc:
            messagebox.showerror("API 配置", str(exc), parent=self.root)
            return False
        self._config_error = ""
        return True

    # ---------- UI ----------

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill=tk.BOTH, expand=True)

        # data files
        file_row = ttk.Frame(main)
        file_row.pack(fill=tk.X)
        ttk.Label(file_row, text="数据文件:").pack(side=tk.LEFT)
        ttk.Button(file_row, text="＋添加文件", command=self.add_files).pack(side=tk.LEFT, padx=4)
        ttk.Button(file_row, text="－移除选中", command=self.remove_selected).pack(side=tk.LEFT, padx=4)
        ttk.Button(file_row, text="清空", command=self.clear_files).pack(side=tk.LEFT, padx=4)

        list_frame = ttk.Frame(main)
        list_frame.pack(fill=tk.X, pady=(4, 8))
        self.file_list = tk.Listbox(list_frame, height=4)
        scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.file_list.yview)
        self.file_list.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.file_list.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # question
        question_row = ttk.Frame(main)
        question_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(question_row, text="问题:").pack(side=tk.LEFT)
        self.question_var = tk.StringVar()
        entry = ttk.Entry(question_row, textvariable=self.question_var)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        entry.bind("<Return>", lambda _event: self.start_analysis())
        self.analyze_button = ttk.Button(question_row, text="▶ 分析", command=self.start_analysis)
        self.analyze_button.pack(side=tk.LEFT)

        # result area: notebook with table / chart tabs
        notebook = ttk.Notebook(main)
        notebook.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        table_tab = ttk.Frame(notebook, padding=4)
        notebook.add(table_tab, text="表格")
        self.table = ttk.Treeview(table_tab, show="headings")
        table_scroll_y = ttk.Scrollbar(table_tab, orient=tk.VERTICAL, command=self.table.yview)
        table_scroll_x = ttk.Scrollbar(table_tab, orient=tk.HORIZONTAL, command=self.table.xview)
        self.table.configure(yscrollcommand=table_scroll_y.set, xscrollcommand=table_scroll_x.set)
        table_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        table_scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.table.pack(fill=tk.BOTH, expand=True)

        chart_tab = ttk.Frame(notebook, padding=4)
        notebook.add(chart_tab, text="可视化")
        self._build_chart_panel(chart_tab)

        # executed SQL
        sql_frame = ttk.LabelFrame(main, text="执行 SQL", padding=4)
        sql_frame.pack(fill=tk.X, pady=(8, 0))
        self.sql_text = tk.Text(sql_frame, height=4, font=("Consolas", 9), state=tk.DISABLED, wrap=tk.NONE)
        sql_scroll = ttk.Scrollbar(sql_frame, orient=tk.VERTICAL, command=self.sql_text.yview)
        self.sql_text.configure(yscrollcommand=sql_scroll.set)
        sql_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.sql_text.pack(fill=tk.X)

        # actions
        action_row = ttk.Frame(main)
        action_row.pack(fill=tk.X, pady=(8, 0))
        self.trace_button = ttk.Button(action_row, text="查看执行轨迹", command=self.show_trace, state=tk.DISABLED)
        self.trace_button.pack(side=tk.LEFT)
        ttk.Button(action_row, text="API 配置", command=self.show_api_config).pack(side=tk.LEFT, padx=6)

        # status bar
        self.status_var = tk.StringVar(value="就绪")
        status = ttk.Label(main, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W, padding=(4, 2))
        status.pack(fill=tk.X, pady=(8, 0))

    # ---------- file list ----------

    def add_files(self) -> None:
        chosen = filedialog.askopenfilenames(title="选择数据文件", filetypes=FILE_TYPES, parent=self.root)
        known = {p.resolve() for p in self.files}
        for name in chosen:
            path = Path(name)
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                messagebox.showwarning("不支持的格式", f"不支持 {path.suffix or '(无扩展名)'}，已跳过 {path.name}", parent=self.root)
                continue
            if path.resolve() not in known:
                self.files.append(path)
                known.add(path.resolve())
        self._refresh_file_list()

    def remove_selected(self) -> None:
        for index in reversed(self.file_list.curselection()):
            self.files.pop(index)
        self._refresh_file_list()

    def clear_files(self) -> None:
        self.files.clear()
        self._refresh_file_list()

    def _refresh_file_list(self) -> None:
        self.file_list.delete(0, tk.END)
        for path in self.files:
            self.file_list.insert(tk.END, str(path))

    # ---------- chart panel ----------

    def _build_chart_panel(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(toolbar, text="图表类型:").pack(side=tk.LEFT)
        self.chart_type_var = tk.StringVar(value="auto")
        self.chart_combo = ttk.Combobox(
            toolbar,
            textvariable=self.chart_type_var,
            values=("auto", "bar", "line", "pie"),
            state="readonly",
            width=12,
        )
        self.chart_combo.pack(side=tk.LEFT, padx=6)
        self.chart_combo.bind("<<ComboboxSelected>>", lambda _event: self.redraw_chart())

        ttk.Label(toolbar, text="X轴:").pack(side=tk.LEFT, padx=(14, 0))
        self.x_var = tk.StringVar(value="auto")
        self.x_combo = ttk.Combobox(toolbar, textvariable=self.x_var, values=("auto",), state="readonly", width=12)
        self.x_combo.pack(side=tk.LEFT, padx=4)
        self.x_combo.bind("<<ComboboxSelected>>", lambda _event: self.redraw_chart())

        ttk.Label(toolbar, text="Y轴:").pack(side=tk.LEFT, padx=(14, 0))
        self.y_var = tk.StringVar(value="auto")
        self.y_combo = ttk.Combobox(toolbar, textvariable=self.y_var, values=("auto",), state="readonly", width=12)
        self.y_combo.pack(side=tk.LEFT, padx=4)
        self.y_combo.bind("<<ComboboxSelected>>", lambda _event: self.redraw_chart())

        self.redraw_button = ttk.Button(toolbar, text="重新绘制", command=self.redraw_chart)
        self.redraw_button.pack(side=tk.LEFT, padx=(14, 0))
        self.chart_hint = tk.StringVar()
        ttk.Label(toolbar, textvariable=self.chart_hint, foreground="#888").pack(side=tk.RIGHT)

        self.chart_area = ttk.Frame(parent)
        self.chart_area.pack(fill=tk.BOTH, expand=True)
        self._chart_placeholder()

    def _chart_placeholder(self, text: str = "分析完成后在此显示图表") -> None:
        for widget in self.chart_area.winfo_children():
            widget.destroy()
        ttk.Label(self.chart_area, text=text, anchor=tk.CENTER).pack(fill=tk.BOTH, expand=True)

    def _refresh_axis_options(self, columns: list[str]) -> None:
        options = ("auto",) + tuple(columns)
        for combo, var in ((self.x_combo, self.x_var), (self.y_combo, self.y_var)):
            combo.configure(values=options)
            if var.get() not in options:
                var.set("auto")

    def redraw_chart(self) -> None:
        if not MATPLOTLIB_OK:
            self._chart_placeholder("未安装 matplotlib，可视化不可用。请运行: pip install matplotlib")
            return
        if self.result is None or self.result.status != "completed" or not self.result.rows:
            self._chart_placeholder("无数据可绘制")
            return
        from charts import make_chart

        try:
            figure = make_chart(self.result, self.chart_type_var.get(), self.x_var.get(), self.y_var.get())
        except ValueError as exc:
            self._chart_placeholder(str(exc))
            self.chart_hint.set("")
            return
        if figure is None:
            self._chart_placeholder("当前结果无法绘制图表（缺少数值列）")
            self.chart_hint.set("")
            return
        for widget in self.chart_area.winfo_children():
            widget.destroy()
        canvas = FigureCanvasTkAgg(figure, master=self.chart_area)
        canvas.draw()
        NavigationToolbar2Tk(canvas, self.chart_area)
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.chart_hint.set(f"类型: {self.chart_type_var.get()} | X: {self.x_var.get()} | Y: {self.y_var.get()}")

    # ---------- analysis ----------

    def start_analysis(self) -> None:
        question = self.question_var.get().strip()
        if not question:
            messagebox.showinfo("提示", "请输入分析问题", parent=self.root)
            return
        if self.model is None:
            messagebox.showerror("未配置 API key", f"{self._config_error}\n请点击“API 配置”按钮补充。", parent=self.root)
            return
        self.analyze_button.configure(state=tk.DISABLED)
        self.trace_button.configure(state=tk.DISABLED)
        self.status_var.set("分析中…")
        started = time.perf_counter()
        model = self.model

        def worker() -> None:
            try:
                result = run_analysis(list(self.files), question, model)
                self.queue.put(("done", (result, time.perf_counter() - started)))
            except Exception as exc:  # noqa: BLE001 - surface any failure in the UI
                self.queue.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def destroy(self) -> None:
        """Cancel the pending queue poll and close the window cleanly."""
        if self._poll_job is not None:
            try:
                self.root.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None
        self.root.destroy()

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "done":
                    result, elapsed = payload
                    self._show_result(result, elapsed)
                else:
                    self.status_var.set(f"失败：{payload}")
        except queue.Empty:
            pass
        self._poll_job = self.root.after(50, self._poll_queue)

    def _show_result(self, result: AnalysisResult, elapsed: float) -> None:
        self.result = result
        self._set_sql_text(result.sql or result.report)
        self.table.delete(*self.table.get_children())
        self.table.configure(columns=())
        if result.status == "completed":
            columns = result.columns or []
            self.table.configure(columns=columns)
            for column in columns:
                self.table.heading(column, text=column)
                self.table.column(column, width=self._column_width(column, result.rows), anchor=tk.W)
            for row in result.rows:
                self.table.insert("", tk.END, values=tuple(row))
            self.status_var.set(f"完成（{elapsed:.1f}s，{len(result.rows)} 行）")
            self.trace_button.configure(state=tk.NORMAL)
            self._refresh_axis_options(result.columns)
            self.redraw_chart()
        else:
            self.status_var.set(f"{result.status}：{result.report}")
            self._set_sql_text(result.report)
            self._chart_placeholder(f"{result.status}：无法可视化")
        self.analyze_button.configure(state=tk.NORMAL)

    def _column_width(self, column: str, rows: list[tuple[object, ...]]) -> int:
        index = self.result.columns.index(column) if self.result else 0
        longest = len(str(column))
        for row in rows:
            if index < len(row):
                longest = max(longest, len(str(row[index])))
        return max(60, longest * 14 + 24)

    def _set_sql_text(self, text: str) -> None:
        self.sql_text.configure(state=tk.NORMAL)
        self.sql_text.delete("1.0", tk.END)
        self.sql_text.insert("1.0", text)
        self.sql_text.configure(state=tk.DISABLED)

    # ---------- dialogs ----------

    def show_trace(self) -> None:
        if self.result is None:
            return
        window = tk.Toplevel(self.root)
        window.title("执行轨迹")
        window.geometry("640x480")
        text = tk.Text(window, font=("Consolas", 9))
        scroll = ttk.Scrollbar(window, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        text.pack(fill=tk.BOTH, expand=True)
        text.insert("1.0", json.dumps(self.result.trace, ensure_ascii=False, indent=2))
        text.configure(state=tk.DISABLED)

    def show_api_config(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("API 配置")
        window.geometry("520x260")
        window.transient(self.root)
        window.grab_set()

        current = self.model
        defaults = {
            "api_key": current.api_key if current else "",
            "base_url": current.base_url if current else "https://api.openai.com/v1",
            "model": current.model if current else "gpt-4o-mini",
        }
        vars_: dict[str, tk.StringVar] = {}
        form = ttk.Frame(window, padding=12)
        form.pack(fill=tk.BOTH, expand=True)
        for row, (key, label) in enumerate(
            (("api_key", "API Key"), ("base_url", "Base URL"), ("model", "Model"))
        ):
            ttk.Label(form, text=f"{label}:").grid(row=row, column=0, sticky=tk.W, pady=4)
            vars_[key] = tk.StringVar(value=defaults[key])
            ttk.Entry(form, textvariable=vars_[key], width=52).grid(row=row, column=1, sticky=tk.EW, pady=4)
        form.columnconfigure(1, weight=1)

        save_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(form, text="同时写入项目 .env 文件", variable=save_var).grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=8)

        buttons = ttk.Frame(window, padding=12)
        buttons.pack(fill=tk.X)

        def apply() -> None:
            if not self._refresh_model(vars_["api_key"].get(), vars_["base_url"].get(), vars_["model"].get()):
                return
            if save_var.get():
                env_path = Path(__file__).resolve().parent / ".env"
                write_env_file(
                    env_path,
                    {
                        "OPENAI_API_KEY": vars_["api_key"].get().strip(),
                        "OPENAI_BASE_URL": vars_["base_url"].get().strip(),
                        "OPENAI_MODEL": vars_["model"].get().strip(),
                    },
                )
            self.status_var.set("API 配置已更新")
            window.destroy()

        ttk.Button(buttons, text="应用", command=apply).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="取消", command=window.destroy).pack(side=tk.RIGHT, padx=6)


def main() -> None:
    root = tk.Tk()
    AnalysisGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
