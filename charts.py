"""Chart rendering for analysis results (matplotlib, optional dependency).

charts.make_chart(result, chart_type) returns a matplotlib Figure for embedding in
the Tkinter GUI. No backend is forced here — the GUI uses FigureCanvasTkAgg; tests
can set MPLBACKEND=Agg. If matplotlib is not installed, everything degrades
gracefully (make_chart returns None).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from core import AnalysisResult

if TYPE_CHECKING:
    from matplotlib.figure import Figure

TIME_COLUMN_RE = re.compile(r"月|年|日|日期|date|month|year|time", re.IGNORECASE)
FONT_CANDIDATES = ["Microsoft YaHei", "SimHei", "PingFang SC", "Noto Sans CJK SC", "WenQuanYi Micro Hei"]
CHART_TYPES = ("auto", "bar", "line", "pie")

_fonts_configured = False


def _configure_fonts() -> None:
    """Pick the first available CJK font so Chinese labels render correctly."""
    global _fonts_configured
    if _fonts_configured:
        return
    import matplotlib
    from matplotlib import font_manager

    available = {font.name for font in font_manager.fontManager.ttflist}
    for candidate in FONT_CANDIDATES:
        if candidate in available:
            matplotlib.rcParams["font.sans-serif"] = [candidate]
            break
    matplotlib.rcParams["axes.unicode_minus"] = False
    _fonts_configured = True


def _numeric_columns(result: AnalysisResult) -> list[int]:
    """Column indexes whose non-null values all convert to float."""
    numeric: list[int] = []
    for index, column in enumerate(result.columns):
        values = [row[index] for row in result.rows if row[index] is not None and str(row[index]).strip() != ""]
        if not values:
            continue
        try:
            for value in values:
                float(value)
        except (TypeError, ValueError):
            continue
        numeric.append(index)
    return numeric


def _time_columns(result: AnalysisResult) -> list[int]:
    return [index for index, column in enumerate(result.columns) if TIME_COLUMN_RE.search(column)]


def infer_chart_type(result: AnalysisResult) -> str | None:
    """Pick a chart type from the result shape: time column -> line, category+number -> bar,
    bare numbers -> line, nothing plottable -> None."""
    numeric = _numeric_columns(result)
    if not numeric:
        return None
    if _time_columns(result):
        return "line"
    if len(result.columns) >= 2 and len(numeric) < len(result.columns):
        return "bar"
    return "line"




def _draw_bar(ax, result: AnalysisResult, x_index: int | None, y_index: int) -> None:
    x_values = [str(row[x_index]) for row in result.rows] if x_index is not None else [str(i + 1) for i in range(len(result.rows))]
    y_values = [float(row[y_index]) for row in result.rows]
    ax.bar(x_values, y_values, color="#4C78A8")
    ax.set_xlabel(result.columns[x_index] if x_index is not None else "行序")
    ax.set_ylabel(result.columns[y_index])
    if len(x_values) > 15:
        ax.tick_params(axis="x", rotation=45)
    ax.set_title("柱状图")


def _draw_line(ax, result: AnalysisResult, x_index: int | None, y_index: int | None) -> None:
    numeric = _numeric_columns(result)
    if x_index is not None:
        order = sorted(range(len(result.rows)), key=lambda i: str(result.rows[i][x_index]))
        rows = [result.rows[i] for i in order]
        x_values = [str(row[x_index]) for row in rows]
        x_label = result.columns[x_index]
    else:
        rows = result.rows
        x_values = [str(i + 1) for i in range(len(rows))]
        x_label = "行序"
    plot_indexes = [y_index] if y_index is not None else numeric
    for column_index in plot_indexes:
        ax.plot(x_values, [float(row[column_index]) for row in rows], marker="o", label=result.columns[column_index])
    ax.set_xlabel(x_label)
    if len(x_values) > 15:
        ax.tick_params(axis="x", rotation=45)
    if len(plot_indexes) > 1:
        ax.legend()
    ax.set_title("折线图")


def _draw_pie(ax, result: AnalysisResult, x_index: int | None, y_index: int) -> None:
    labels = [str(row[x_index]) for row in result.rows] if x_index is not None else [f"项 {i + 1}" for i in range(len(result.rows))]
    sizes = [float(row[y_index]) for row in result.rows]
    if len(labels) > 12:
        keep, rest = labels[:11], sum(sizes[11:])
        labels, sizes = keep + ["其他"], sizes[:11] + [rest]
    ax.pie(sizes, labels=labels, autopct="%1.1f%%", startangle=90)
    ax.set_title("饼图")


def make_chart(
    result: AnalysisResult,
    chart_type: str | None = None,
    x_column: str | None = None,
    y_column: str | None = None,
) -> "Figure | None":
    """Build a matplotlib Figure for the result.

    chart_type: auto/bar/line/pie. x_column/y_column optionally pin specific
    result columns to the X (category/time) and Y (value) axes; "auto"/None picks
    them automatically. Raises ValueError for unknown columns or a non-numeric
    Y column. Returns None when nothing can be plotted (or matplotlib is missing).
    """
    if result.status != "completed" or not result.rows:
        return None
    columns = result.columns
    numeric = _numeric_columns(result)
    if not numeric:
        return None

    x_index = None
    if x_column not in (None, "", "auto"):
        if x_column not in columns:
            raise ValueError(f"未找到列: {x_column}")
        x_index = columns.index(x_column)
    y_index = None
    if y_column not in (None, "", "auto"):
        if y_column not in columns:
            raise ValueError(f"未找到列: {y_column}")
        y_index = columns.index(y_column)
        if y_index not in numeric:
            raise ValueError(f"Y 轴列“{y_column}”不是数值列，无法绘制")

    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    _configure_fonts()

    if chart_type in (None, "auto"):
        if x_index is not None and y_index is not None:
            chart_type = "line" if TIME_COLUMN_RE.search(columns[x_index]) else "bar"
        else:
            chart_type = infer_chart_type(result)
            if chart_type is None:
                return None
    if y_index is None:
        y_index = numeric[0]
    if x_index is None:
        categories = [index for index in range(len(columns)) if index not in numeric]
        x_index = categories[0] if categories else None

    figure, ax = plt.subplots(figsize=(7, 4.5))
    figure.tight_layout()
    if chart_type == "bar":
        _draw_bar(ax, result, x_index, y_index)
    elif chart_type == "line":
        _draw_line(ax, result, x_index, y_index)
    elif chart_type == "pie":
        _draw_pie(ax, result, x_index, y_index)
    else:
        raise ValueError(f"未知图表类型: {chart_type}")
    return figure
