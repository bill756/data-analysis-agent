# 数据分析Agent

多角色 NL2SQL 数据分析 Agent：接入**在线大模型**（OpenAI 兼容接口），支持 SQLite 与 txt/csv/xlsx/xls/json 六种数据源，将自然语言问题转换为**可审查的只读 SQL** 并输出分析报告。提供 **CLI** 与 **Tkinter 桌面 GUI**（含 matplotlib 可视化）两种使用方式。

## 功能总览

| 维度 | 能力 |
|------|------|
| 数据源 | `.db` / `.txt` / `.csv` / `.xlsx` / `.xls` / `.json`，多文件可跨表查询 |
| 模型层 | OpenAI 兼容 `chat/completions`（DeepSeek / OpenAI / 通义 / 智谱 / Ollama…），纯标准库 `urllib` 实现 |
| 分析流水线 | 三个 LLM 角色 Planner → SQL Author → Reviewer，复核失败带反馈自动重试，报告本地生成 |
| 安全 | 静态校验（单条 SELECT / 关键字黑名单 / 已知表引用）+ SQLite authorizer 只读执行 + 步数/行数限制，任意输入无法写入数据 |
| 界面 | CLI（Markdown 报告 / JSON 轨迹）+ Tkinter GUI（结果表格 / 可视化图表） |
| 配置 | `.env` 文件 / 环境变量 / 命令行参数三级优先级 |

## 快速开始

```bash
# 安装依赖（Excel 与可视化支持；txt/json 与核心逻辑零依赖）
pip install -r requirements.txt

# 方式一：.env 配置文件（推荐，自动加载项目根目录 .env，无需任何参数）
#   在项目根目录创建 .env：
#     OPENAI_API_KEY=sk-xxx
#     OPENAI_BASE_URL=https://api.deepseek.com/v1   # 可选，默认 OpenAI
#     OPENAI_MODEL=deepseek-v4-flash                # 可选，默认 gpt-4o-mini
python cli.py "各部门销售额汇总" --file ./sales.csv
python cli.py "各部门销售额汇总" --db ./sales.db

# 方式二：环境变量配置 API
set OPENAI_API_KEY=sk-xxx            # Windows CMD
export OPENAI_API_KEY=sk-xxx         # bash

# 方式三：命令行参数覆盖（优先级最高）
python cli.py "各部门销售额汇总" --file ./sales.json     --base-url https://api.deepseek.com/v1 --model deepseek-v4-flash --api-key sk-xxx

# 指定 .env 文件路径（默认取项目根 .env）
python cli.py "各部门销售额汇总" --file ./sales.xlsx --env-file ./config/prod.env

# 输出 JSON 轨迹
python cli.py "各部门销售额汇总" --file ./sales.xlsx --json

# 启动图形界面
python gui.py
```

配置优先级：**命令行参数 > 系统环境变量 > `.env` 文件 > 内置默认值**。`.env` 请勿提交到版本库（仓库不含 `.env` 与 `.gitignore`，clone 后自行创建 `.env`）。

## 分析流水线

`DataAnalysisAgent.run()` 内部为六步流水线，每一步记录进 `trace`（`--json` 或 GUI"执行轨迹"可查看）：

| 步骤 | 说明 |
|------|------|
| 1. Schema 检查 | 自动读取表结构与列类型（`inspect_schema`） |
| 2. 计划生成 | LLM 根据问题选择相关表（`AnalystModel.plan`） |
| 3. SQL 生成 | LLM 生成 SQL，被拒时带反馈重试（默认最多 1 次） |
| 4. 安全校验 | `SqlGuard` 静态拦截危险 SQL |
| 5. 独立复核 | LLM 二次验证 SQL 是否覆盖计划表 |
| 6. 只读执行 + 报告 | 以只读模式执行，输出 Markdown 报告 |

## 多格式数据源

| 参数 | 格式 | 说明 |
|------|------|------|
| `--file` | `.txt` / `.csv` | 逗号/制表/分号/竖线自动探测；编码 utf-8-sig → gbk 回退 |
| `--file` | `.xlsx` / `.xls` | 读取第一个非空 sheet，单元格值（非公式） |
| `--file` | `.json` | 对象数组 / 含对象数组字段 / 单对象（嵌套对象转字符串） |
| `--db` | `.db` | SQLite 原生只读连接（`mode=ro`） |

- `--file` 可重复传入，多文件加载进同一内存库（多张表，可跨表 JOIN）
- 表名 = 文件名（自动清洗）；列名原样保留（中文/空格可用），SQL 中双引号引用
- 列类型自动推断（INTEGER/REAL/TEXT），保证 `SUM` 等聚合可用

## 安全边界

| 层级 | 机制 |
|------|------|
| SQL关键字 | 黑名单拦截 INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/PRAGMA 等 |
| 语句数量 | 仅允许单条SQL |
| 语句类型 | 仅允许 SELECT / WITH 开头 |
| 表引用 | 仅允许查询已知表（含带引号标识符） |
| 执行层 | `sqlite3` authorizer 只放行 SELECT/READ/FUNCTION |
| 进程超限 | 单次查询最多10000步后中断；结果默认截断100行 |

## 图形界面（GUI）

```bash
python gui.py
```

基于 Tkinter（标准库，零依赖）。功能：

- **数据文件**：添加（多选）/移除/清空；支持 `.db/.txt/.csv/.xlsx/.xls/.json`
- **问题输入**：回车或点击"▶ 分析"；分析在后台线程执行，界面不卡顿
- **结果表格**：自动适配列宽；下方显示执行 SQL
- **可视化**（需 `pip install matplotlib`）：
  - 结果区"可视化"标签页，图表类型下拉（自动/柱状/折线/饼图）
  - **X轴/Y轴列指定**：如"ID→X轴、Count→Y轴"，选择即时重绘；只指定一个轴时另一轴自动补
  - 自动推断规则：时间列→折线、分类+数值→柱状、纯数值→折线
  - 中文标签自动适配系统字体；Y 轴指定非数值列时给出明确提示
  - matplotlib 工具栏支持缩放/平移/保存图片
- **执行轨迹**：查看完整流水线（inspect_schema → plan → guard → review → execute）
- **API 配置**：图形化修改 key/base-url/model，可选写入 `.env`（默认不写）
- 缺 key 不崩溃：状态栏提示，点击"API 配置"即可补充

## 扩展自定义模型

实现 `AnalystModel` 协议（`plan` / `write_sql` / `review` 三个方法），传入 `DataAnalysisAgent` 即可替换在线模型：

```python
from pathlib import Path
from core import DataAnalysisAgent, connect_read_only

class MyAnalyst:
    def plan(self, question, schema): ...
    def write_sql(self, question, plan, schema, attempt, feedback): ...
    def review(self, question, plan, sql, schema): ...

conn = connect_read_only(Path("data.db"))
agent = DataAnalysisAgent(conn, model=MyAnalyst())
print(agent.run("各产品销量趋势").report)
```

## 项目结构

```
数据分析Agent/
├── __init__.py        # 包入口
├── core.py            # 核心逻辑：SqlGuard, DataAnalysisAgent, AnalystModel 协议
├── openai_model.py    # 在线模型：OpenAIAnalyst（OpenAI 兼容接口，标准库实现，支持 .env）
├── loaders.py         # 多格式解析：txt/csv/xlsx/xls/json → 内存 SQLite
├── gui.py             # Tkinter 图形界面（零依赖，后台线程 + 可视化标签页）
├── charts.py          # 可视化：查询结果 → matplotlib 图表（可选依赖，支持坐标列指定）
├── cli.py             # 命令行入口
├── README.md          # 本文件
└── requirements.txt   # 依赖清单（openpyxl/xlrd/matplotlib 均为可选）
```
