"""Tests for gui.py: run_analysis logic against the mock LLM server, plus a GUI smoke test.

Usage: python scripts/gui_test.py
"""

from __future__ import annotations

import sys
import threading
from http.server import HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui import AnalysisGUI, run_analysis  # noqa: E402
from openai_model import OpenAIAnalyst  # noqa: E402
from scripts.mock_llm_server import Handler  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def start_mock(mode: str) -> tuple[HTTPServer, OpenAIAnalyst]:
    Handler.mode = mode
    Handler.review_calls = 0
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    return server, OpenAIAnalyst(api_key="test", base_url=base_url, model="mock")


def test_logic() -> None:
    server, model = start_mock("normal")

    print("[1] 五种格式 run_analysis")
    for ext in ("csv", "txt", "xlsx", "xls", "json"):
        result = run_analysis([EXAMPLES / f"sales.{ext}"], "各部门销售额汇总", model)
        check(f"{ext}: completed", result.status == "completed", result.report[:200])
        check(f"{ext}: 含研发", "研发" in result.report, result.report[:100])

    print("[2] 多文件混合")
    result = run_analysis([EXAMPLES / "sales.txt", EXAMPLES / "sales.json"], "各部门销售额汇总", model)
    check("混合 completed", result.status == "completed", result.report[:200])

    print("[3] 输入校验")
    for name, files, question in (
        ("无文件", [], "问题"),
        ("空问题", [EXAMPLES / "sales.csv"], "   "),
        ("db+非db混用", [ROOT / "sales.db", EXAMPLES / "sales.csv"], "问题"),
        ("多个db", [ROOT / "sales.db", ROOT / "sales.db"], "问题"),
    ):
        try:
            run_analysis(files, question, model)
            check(f"{name} 应报错", False, "未抛出异常")
        except ValueError:
            check(f"{name} 报错", True)
    server.shutdown()

    print("[4] 恶意 SQL 拦截")
    server, model = start_mock("evil_sql")
    result = run_analysis([EXAMPLES / "sales.xlsx"], "各部门销售额汇总", model)
    check("rejected", result.status == "rejected", result.report[:200])
    server.shutdown()

    print("[5] review 拒绝后重试")
    server, model = start_mock("reject_once")
    result = run_analysis([EXAMPLES / "sales.xlsx"], "各部门销售额汇总", model)
    check("重试后 completed", result.status == "completed", result.report[:200])
    server.shutdown()

    print("[6] 模型非法 JSON")
    server, model = start_mock("bad_json")
    result = run_analysis([EXAMPLES / "sales.json"], "各部门销售额汇总", model)
    check("error 状态", result.status == "error", result.report[:200])
    server.shutdown()

    print("[7] --db 等价路径")
    server, model = start_mock("normal")
    result = run_analysis([ROOT / "sales.db"], "各部门销售额汇总", model)
    check("db completed", result.status == "completed", result.report[:200])
    server.shutdown()


def test_gui_smoke() -> None:
    print("[8] GUI 初始化冒烟 + 可视化")
    import time
    import tkinter as tk

    from core import AnalysisResult

    def pump(rounds: int = 30) -> None:
        for _ in range(rounds):
            root.update()
            time.sleep(0.02)

    root = tk.Tk()
    root.withdraw()
    gui = AnalysisGUI(root)
    pump(5)
    check("GUI 构建成功", gui is not None)
    check("状态栏就绪", gui.status_var.get() == "就绪", gui.status_var.get())

    # 模拟 completed 结果 → 自动绘制
    result = AnalysisResult(
        "completed", "SELECT 1", ["部门", "销售额"], [("研发", 2200), ("市场", 1800), ("销售", 200)], "report", []
    )
    gui._show_result(result, 0.5)
    pump()
    check("completed 自动绘制", len(gui.chart_area.winfo_children()) > 0, str(len(gui.chart_area.winfo_children())))
    check("状态栏完成", gui.status_var.get().startswith("完成"), gui.status_var.get())

    # 切换饼图 → 重绘
    gui.chart_type_var.set("pie")
    gui.redraw_chart()
    pump()
    check("饼图重绘", len(gui.chart_area.winfo_children()) > 0, str(len(gui.chart_area.winfo_children())))

    # 坐标指定：X=部门, Y=销售额 → 重绘
    check("X轴下拉已填充列", "部门" in gui.x_combo["values"], str(gui.x_combo["values"]))
    check("Y轴下拉已填充列", "销售额" in gui.y_combo["values"], str(gui.y_combo["values"]))
    gui.chart_type_var.set("bar")
    gui.x_var.set("部门")
    gui.y_var.set("销售额")
    gui.redraw_chart()
    pump()
    check("坐标指定重绘", len(gui.chart_area.winfo_children()) > 0, str(len(gui.chart_area.winfo_children())))
    check("hint 显示坐标", "X: 部门" in gui.chart_hint.get() and "Y: 销售额" in gui.chart_hint.get(), gui.chart_hint.get())

    # 指定不存在的列 → 占位提示
    gui.y_var.set("不存在的列")
    gui.redraw_chart()
    pump(5)
    placeholder_texts = [w.cget("text") for w in gui.chart_area.winfo_children()]
    check("非法列提示", any("未找到列" in t for t in placeholder_texts), str(placeholder_texts))
    gui.y_var.set("auto")

    # 无可视化数据 → 占位提示
    gui.chart_type_var.set("auto")
    gui._show_result(AnalysisResult("rejected", "DROP", [], [], "SQL 安全校验失败", []), 0.5)
    pump(5)
    placeholder_texts = [w.cget("text") for w in gui.chart_area.winfo_children()]
    check("rejected 占位提示", any("无法可视化" in t for t in placeholder_texts), str(placeholder_texts))

    gui.destroy()
    check("GUI 正常退出", True)


def main() -> None:
    test_logic()
    test_gui_smoke()
    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
