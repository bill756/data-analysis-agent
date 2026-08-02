# 数据分析Agent

纯CLI、多角色NL2SQL Agent，接入**在线大模型**（OpenAI 兼容接口），对 SQLite 及常见表格文件（txt/csv、xlsx/xls、json）进行**只读**数据分析。

## 核心能力

- **多格式数据源**：`.db`（原生只读连接）、`.txt/.csv`（自动探测分隔符，utf-8/gbk 编码）、`.xlsx/.xls`（首个非空 sheet）、`.json`（对象数组/嵌套数组/单对象）
- **在线 AI 模型**：OpenAI 兼容 `chat/completions` 接口，通过 `base_url`/`api_key`/`model` 可切换任意兼容服务（OpenAI、DeepSeek、通义、智谱、本地 Ollama 等）；**已彻底移除离线模型**
- **Schema检查**：自动读取表结构与类型
- **计划生成**：LLM 根据自然语言问题选择相关表
- **SQL生成 + 安全校验**：`SqlGuard` 拦截所有写入/DDL/PRAGMA/未知表/多语句
- **独立复核**：LLM 二次验证 SQL 是否覆盖计划表；失败自动带反馈重试
- **只读执行**：`sqlite3` authorizer 只放行 SELECT/READ/FUNCTION + `mode=ro` 双重保障
- **Markdown报告**：含表结构的结果报告，`--json` 输出完整执行轨迹
- **零依赖核心**：在线调用仅用标准库 `urllib`，无需 openai SDK

## 快速开始

```bash
# 安装依赖（Excel 格式支持；txt/json 无需）
pip install -r requirements.txt

# 方式一：.env 配置文件（推荐，自动加载项目根目录 .env，无需任何参数）
#   在项目根目录创建 .env：
#     OPENAI_API_KEY=sk-xxx
#     OPENAI_BASE_URL=https://api.deepseek.com/v1   # 可选，默认 OpenAI
#     OPENAI_MODEL=deepseek-v4-flash                # 可选，默认 gpt-4o-mini
python cli.py "各部门销售额汇总" --file ./examples/sales.xlsx
python cli.py "各部门销售额汇总" --db ./sales.db

# 方式二：环境变量配置 API
set OPENAI_API_KEY=sk-xxx            # Windows CMD
export OPENAI_API_KEY=sk-xxx         # bash

# 方式三：命令行参数覆盖（优先级最高）
python cli.py "各部门销售额汇总" --file ./examples/sales.json \
    --base-url https://api.deepseek.com/v1 --model deepseek-v4-flash --api-key sk-xxx

# 指定 .env 文件路径（默认取项目根 .env）
python cli.py "各部门销售额汇总" --file ./examples/sales.xlsx --env-file ./config/prod.env

# 输出JSON轨迹
python cli.py "各部门销售额汇总" --file ./examples/sales.xlsx --json
```

配置优先级：**命令行参数 > 系统环境变量 > `.env` 文件 > 内置默认值**。`.env` 已被 `.gitignore` 排除，不会误提交密钥。

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

## 无 API key 离线演示（本地 mock）

```bash
python scripts/mock_llm_server.py --port 8765            # 模式: normal / evil_sql / reject_once / bad_json / auth_error
python cli.py "各部门销售额汇总" --file ./examples/sales.xlsx \
    --base-url http://127.0.0.1:8765/v1 --api-key test-key
```

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

## 图形界面（GUI）

```bash
python gui.py
```

基于 Tkinter（标准库，零依赖）。功能：

- **数据文件**：添加（多选）/移除/清空；支持 `.db/.txt/.csv/.xlsx/.xls/.json`
- **问题输入**：回车或点击"▶ 分析"；分析在后台线程执行，界面不卡顿
- **结果表格**：自动适配列宽；下方显示执行 SQL
- **可视化**（需 `pip install matplotlib`）：结果区"可视化"标签页，图表类型下拉（自动/柱状/折线/饼图）+ **X轴/Y轴列指定**（如"ID→X轴、Count→Y轴"，选择即时重绘），支持缩放/平移/保存；自动推断规则：时间列→折线、分类+数值→柱状、纯数值→折线；中文标签自动适配系统字体；Y 轴指定非数值列时给出明确提示
- **执行轨迹**：查看完整流水线（inspect_schema → plan → guard → review → execute）
- **API 配置**：图形化修改 key/base-url/model，可选写入 `.env`（默认不写）
- 缺 key 不崩溃：状态栏提示，点击"API 配置"即可补充

## 测试

```bash
python scripts/e2e_test.py      # CLI mock 端到端（25 项断言，无需真实 API key）
python scripts/gui_test.py      # GUI 逻辑层 + 界面冒烟（22 项断言）
python scripts/make_examples.py # 重新生成 examples/ 样例文件
```

## 项目结构

```
数据分析Agent/
├── __init__.py        # 包入口
├── core.py            # 核心逻辑：SqlGuard, DataAnalysisAgent, AnalystModel 协议
├── openai_model.py    # 在线模型：OpenAIAnalyst（OpenAI 兼容接口，标准库实现）
├── loaders.py         # 多格式解析：txt/csv/xlsx/xls/json → 内存 SQLite
├── gui.py             # Tkinter 图形界面（零依赖）
├── charts.py          # 可视化：查询结果 → matplotlib 图表（可选依赖）
├── cli.py             # 命令行入口
├── examples/          # 样例数据（csv/txt/xlsx/xls/json）
├── scripts/
│   ├── make_examples.py   # 样例生成脚本
│   ├── mock_llm_server.py # 本地 mock LLM 服务器（离线演示/测试）
│   └── e2e_test.py        # 端到端测试
├── README.md          # 本文件
└── requirements.txt   # 依赖清单
```
