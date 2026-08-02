# 数据可视化设计文档（2026-08-03）

## 背景

"数据分析Agent"已具备 CLI + Tkinter GUI。需求（用户已确认）：新增**数据可视化**功能——用 **matplotlib** 实现，**仅 GUI 内嵌图表展示**（自动推断 + 手动切换图表类型）；**CLI 不做导出**。

## 总体架构

新增 `charts.py`（纯函数，基于 matplotlib `Agg` 后端），复用 `AnalysisResult`：

```
AnalysisResult ──► charts.make_chart(result, chart_type)
                      │
                      └─► GUI：FigureCanvasTkAgg 嵌入"可视化"标签页（主线程绘制）
```

matplotlib 为**可选依赖**（`requirements.txt` 标注），charts.py 顶部延迟 import——未安装时 GUI 隐藏可视化页、CLI 报清晰错误。

## 图表类型与自动推断

支持三种类型：`bar`（柱状）/ `line`（折线）/ `pie`（饼图），另有 `auto` 自动推断：

| auto 推断规则 | 图表 |
|--------------|------|
| 存在时间类列（列名含 月/年/日/date/month/year/time 等） | `line` |
| 存在分类列（TEXT）+ 数值列 | `bar` |
| 无分类列但有多列数值 | 多序列 `line`（x=行序号） |
| 无法识别数值列 | 不可视化，返回 None + 原因 |

- 数值列判定：该列全部（非空）值可转 float
- `pie` 不自动触发，仅手动选择（避免误用）
- 数据直接来自查询结果（≤100 行，量级合适）

## 图表绘制（charts.py）

- `make_chart(result, chart_type=None) -> matplotlib.figure.Figure | None`：
  - `chart_type=None` 时自动推断
  - 返回 matplotlib Figure 供 GUI 嵌入（不落盘）
  - 无法可视化时返回 `None`（GUI 显示原因提示）
- **中文字体**：启动时检测系统可用字体（Microsoft YaHei / SimHei / PingFang SC / Noto Sans CJK SC），命中即设置 `plt.rcParams["font.sans-serif"]`；同时 `axes.unicode_minus = False`（负号显示）
- 柱状图：x=分类值（转字符串，超出 15 个自动旋转标签），y=首个数值列
- 折线图：时间列排序后绘制（多数值列多序列）；无时间列时按行序
- 饼图：labels=分类列，sizes=数值列；`autopct` 显示百分比；分类 >12 时合并为"其他"

## GUI 扩展（gui.py）

- 结果区由单一 Treeview 改为 `ttk.Notebook` 两个标签页：**"表格"**（现有 Treeview）/ **"可视化"**
- 可视化页布局：
  - 工具栏：`图表类型` 下拉框（自动/柱状/折线/饼图）+ "重新绘制"按钮
  - 图表区：`FigureCanvasTkAgg` + NavigationToolbar2Tk（缩放/平移/保存）
- 触发时机：分析完成（completed 且 rows>0）自动按当前下拉框选择绘制；切换下拉框即时重绘
- 绘制在主线程同步执行（结果 ≤100 行，matplotlib 绘制 <0.1s，无需后台线程）
- 状态为 rejected/error 或 rows=0 时：可视化页显示"无数据可绘制"提示
- 未安装 matplotlib：可视化标签页显示安装提示文字，工具栏禁用

## 依赖与文档

- `requirements.txt`：新增 `matplotlib>=3.8`（标注：GUI 可视化可选；不装则 CLI 与 GUI 表格功能不受影响，可视化页显示提示）
- `README.md`：新增可视化章节（GUI 标签页用法、字体说明）

## 测试验证

1. `charts.make_chart` 单元验证（Agg 后端，无显示环境）：auto 推断规则（时间列→line、分类+数值→bar、纯数值→line）、三种类型各生成 Figure 并 savefig PNG（存在且非空）、中文标签无异常
2. GUI 冒烟：可视化标签页构建、模拟 completed 结果触发绘制、未装 matplotlib 时提示路径
3. 真实 DeepSeek：run_analysis 结果经 make_chart 绘制确认（中文列名/中文值）

## 不做（YAGNI）

- CLI 图表导出
- 交互式图表配置（颜色/标题自定义）
- 散点图/箱线图等其他类型
- 图表导出格式（PNG/SVG/PDF）
- 大结果集（>100 行）图表聚合
