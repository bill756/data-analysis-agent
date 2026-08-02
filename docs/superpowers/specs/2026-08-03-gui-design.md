# GUI 设计文档（2026-08-03）

## 背景

"数据分析Agent"目前是纯 CLI。需求：增加 **Tkinter 图形界面**（用户已确认：Tkinter 技术栈、完整功能范围），复用现有全部能力（多格式文件加载、在线 LLM 分析、安全校验、轨迹）。

## 总体架构

新增 `gui.py`（单文件，约 300 行），**零新增依赖**（Tkinter 为标准库）：

```
gui.py ──► loaders.load_into_memory / connect_read_only（数据源）
   │
   ├─► DataAnalysisAgent（流水线）
   └─► OpenAIAnalyst（在线模型，.env/环境变量/参数三来源）
```

核心逻辑抽为纯函数 `run_analysis(files, question, model) -> AnalysisResult`，便于无 GUI 环境测试。

## 界面布局（单窗口）

```
┌────────────────────────────────────────────────────┐
│ 数据文件: [＋添加文件] [－移除选中] [清空]           │
│   文件列表 (Listbox: sales.xlsx, sales.json ...)    │
│ 问题: [______________________] [▶ 分析]            │
│ ┌────────────────────────────────────────────────┐ │
│ │ 结果表格 (ttk.Treeview，列自动适配)              │ │
│ └────────────────────────────────────────────────┘ │
│ 执行SQL: (等宽字体 Text 只读区)                     │
│ 状态栏: 就绪 │ 分析中… │ 完成(0.8s)                 │
│ [查看执行轨迹] [API 配置]                            │
└────────────────────────────────────────────────────┘
```

## 功能设计

1. **数据文件区**：`filedialog.askopenfilenames` 多选（过滤 `.db/.txt/.csv/.xlsx/.xls/.json`）；Listbox 展示；移除选中/清空按钮；支持拖拽文件到窗口（`tkdnd` 不可用则跳过，纯按钮操作）
2. **问题输入**：`ttk.Entry` + "分析"按钮，`<Return>` 键触发
3. **后台线程执行**：模型调用是网络阻塞，worker 线程跑 `DataAnalysisAgent.run`，`queue.Queue` + `root.after(50ms)` 轮询回主线程更新 UI（保证不卡界面）；分析期间禁用按钮、状态栏显示"分析中…"
4. **结果展示**：status=completed → Treeview 填 `columns/rows`，下方 Text 显示执行 SQL；rejected/error → Text 区显示原因，状态栏标红
5. **执行轨迹**：`--json` 同款 trace，弹 Toplevel 窗口用 Text 展示 `json.dumps(trace, indent=2, ensure_ascii=False)`
6. **API 配置对话框**（Toplevel）：
   - `api_key` / `base_url` / `model` 三个 Entry，预填当前模型实例的值（来自 .env/环境变量）
   - "应用"按钮：创建新 `OpenAIAnalyst` 实例并替换（不落盘）
   - "应用并写入 .env" 复选框：勾选后把三项写入项目根 `.env`（默认不勾选）
   - 启动时若缺 key（`OpenAIAnalyst()` 抛 ValueError）：不崩溃，状态栏提示"未配置 API key"，点配置对话框可补
7. **状态栏**：就绪 / 分析中… / 完成(n.ns) / 失败原因
8. **多数据源**：与 CLI 一致——只选 `.db` → `connect_read_only`；含非 db → `load_into_memory`（混用 .db 与非 db 时提示不支持）

## 错误处理

- 文件不存在/格式不支持/解析失败 → 状态栏显示错误，不崩溃
- 模型异常（认证失败/超时/JSON 解析失败）→ 状态栏 + 结果区显示 `AnalysisResult.report`（agent 已转为 error 结果）
- 分析中再次点击 → 忽略（按钮已禁用）

## 测试验证

1. `run_analysis` 纯逻辑函数：mock LLM 服务器端到端（5 格式、恶意 SQL 拦截、reject 重试、非法 JSON）
2. GUI 初始化冒烟：`root.withdraw()` + 定时 `destroy()`，验证窗口能正常构建（本地 Windows 有显示环境）
3. 手动运行 GUI 验证交互（用户）

## 不做（YAGNI）

- Markdown 完整渲染（只做表格+SQL 文本，不做富文本）
- 结果导出/图表
- 拖拽文件（tkdnd 依赖）
- 异步取消（分析中不可取消）
