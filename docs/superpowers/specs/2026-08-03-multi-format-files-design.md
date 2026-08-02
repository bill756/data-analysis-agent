# 多格式文件支持 + 在线 AI 模型层 设计文档（2026-08-03）

## 背景

当前"数据分析Agent"仅支持 `.db`（SQLite）文件作为分析数据源，通过 `connect_read_only()` 以 `mode=ro` 原生连接，且模型层只有内置离线模型 `DeterministicAnalyst`。

需求（用户已确认）：
1. 扩展支持 `txt`、`xlsx`/`xls`、`json` 等文件格式
2. **彻底移除离线模型**，接入**在线 AI 服务**（OpenAI 兼容接口 + 可配置端点，可接 OpenAI / DeepSeek / 通义 / 智谱 / Ollama 等任意兼容服务）
3. 两者一起交付

用户已确认的决策：
- 依赖策略：引入 `openpyxl`（读 `.xlsx`）+ `xlrd`（读 `.xls`）；txt/json 仍用标准库
- txt 语义：按 CSV/分隔符文本解析（自动探测分隔符）
- AI 接入：OpenAI 兼容 `chat/completions`，通过 `base_url` / `api_key` / `model` 三配置项可切换任意兼容服务

## 总体架构

不改动 Agent 流水线。所有非 `.db` 文件解析成"表"后灌入内存 SQLite（`:memory:`），后续完全复用现有流水线：

```
txt/csv ─┐
xlsx/xls ┼─→ loaders.py ─→ :memory: SQLite ─→ DataAnalysisAgent（现有流水线不动）
json ────┘
```

新增模块 `loaders.py`，职责单一：文件路径列表 → 内存 SQLite 连接。

## 解析规则

| 格式 | 解析方式 |
|------|---------|
| `.txt` | 标准库 `csv` + `Sniffer` 自动探测分隔符（逗号/制表/分号/竖线）；编码先试 `utf-8-sig`，失败回退 `gbk` |
| `.xlsx` | `openpyxl` 只读模式（`read_only=True, data_only=True`），取第一个非空 sheet，首行作表头 |
| `.xls` | `xlrd`，同样取第一个非空 sheet |
| `.json` | 标准库 `json`：根为对象数组 → 直接成表；根为对象但含数组值 → 取第一个数组值（值为 dict 的数组）；根为纯对象 → 单行表；嵌套 dict 值 → `json.dumps` 字符串 |

通用规则：
- **表名** = 文件名去扩展名，非 `[A-Za-z0-9_]` 字符转 `_`；sanitize 后为空或数字开头时加前缀 `t_`；冲突时追加序号
- **列名** = 原样保留（允许中文/空格），SQL 中统一双引号引用
- **类型推断**：每列全量扫描，仅对非空值判定：全部可转 int → INTEGER，否则全部可转 float → REAL，否则 TEXT；全空列 → TEXT
- **空值**：Excel/CSV 空单元格 → NULL；json 缺键 → NULL
- 空文件/无数据/坏格式 → 明确报错（含文件路径与原因）

## core.py 升级

1. `SqlGuard.validate`：
   - `TABLE_RE` 升级为同时匹配裸标识符与 `"双引号标识符"`（表引用检测）
   - 关键字黑名单检测前，先剔除双引号字符串内容（防止列名恰为 `update` 等被误拦截）
2. **删除 `DeterministicAnalyst`**：`DataAnalysisAgent.model` 参数默认 `None` 时，在 `__init__` 内**延迟导入**并实例化 `OpenAIAnalyst()`（避免 `core ↔ openai_model` 循环导入），保持"默认即在线模型"；库使用者可显式传入自定义 `AnalystModel`
3. `DataAnalysisAgent.run()`：对 `model.plan / write_sql / review` 调用包 try/except，模型异常（网络/解析失败）→ 返回 `status="error"` 的 `AnalysisResult`，避免 CLI 崩溃

## 在线 AI 模型层（新增 `openai_model.py`）

### `OpenAIAnalyst` 实现 `AnalystModel` 协议

- 构造参数：`base_url`（默认 `https://api.openai.com/v1`）、`api_key`（默认读环境变量 `OPENAI_API_KEY`）、`model`（默认 `gpt-4o-mini`）、`timeout`（默认 60s）
- 使用**标准库 `urllib.request`** 同步调用 `POST {base_url}/chat/completions`，不新增第三方依赖（延续项目标准库优先风格）
- 三个方法的 prompt 设计：
  - `plan`：system 提示词描述角色 + 以文本形式给出 schema（`表名(列名: 类型, ...)`），要求返回 JSON `{"goal": "...", "relevant_tables": [...]}`；user 消息为问题
  - `write_sql`：system 给出 schema + 规则（只读 SELECT、引用已知表、结果用 Markdown 表格说明可忽略）；user 消息含问题、计划、`attempt`、历史 `feedback`（若有）；要求输出纯 SQL（可包在 ` ```sql ` 代码块中，解析时提取）
  - `review`：要求返回 JSON `{"approved": bool, "feedback": "..."}`
- `temperature=0`，保证输出稳定
- **输出解析容错**：优先 `json.loads`；失败时尝试提取 ` ```json ` 代码块；再失败提取 `{...}` 子串；仍失败抛 `ValueError`（由 agent 层转为 error 结果）
- **HTTP 错误处理**：401/403 → 提示检查 `OPENAI_API_KEY`；404 → 提示检查 `base_url`；超时/网络错误 → 明确报错信息
- 列名/表名规则提醒：prompt 中明确告知"列名可能含中文/空格，SQL 中必须用双引号引用"

## CLI 接口

```
python cli.py "各部门销售额汇总" --file ./sales.xlsx --file ./notes.txt   # 新：多文件
python cli.py "各部门销售额汇总" --db ./sales.db                          # 旧：完全兼容
```

- `--file`：`action="append"`，可重复传入；按扩展名分派加载器
- `--db` 与 `--file`：argparse 互斥组，二选一必填
- `--json` 轨迹模式不变
- 多文件加载进同一内存库（多表）
- 模型配置（均可选，有默认）：
  - `--api-key`：默认读环境变量 `OPENAI_API_KEY`；两者皆无 → 启动即报错并提示
  - `--base-url`：默认 `https://api.openai.com/v1`
  - `--model`：默认 `gpt-4o-mini`
- `DataAnalysisAgent` 默认使用 `OpenAIAnalyst`；库使用者可传入自定义 `AnalystModel` 实现

## 依赖与文档

- `requirements.txt`：新增 `openpyxl`、`xlrd`（标注可选；txt/json 与在线调用零依赖）
- `README.md`：重写——新用法（`--file` 多格式 + 在线 AI 配置）、格式支持表、模型扩展说明（`AnalystModel` 协议 + `OpenAIAnalyst`）
- 新增 `examples/` 样例目录：sales 语义的 `csv`/`txt`/`xlsx`/`xls`/`json` 各一份

## 测试验证

1. 生成 5 种格式样例文件（复用 sales 语义：department / amount 等列）
2. 逐格式跑 CLI（在线模型用**本地 mock OpenAI 兼容服务器**端到端验证：`python -m http.server` 风格的自写 handler 返回固定 JSON，`--base-url` 指向本地，无真实 API key 也可全流程验证）
3. mock 验证 `--json` 轨迹、行数截断、恶意 SQL 拦截（安全机制在内存库上依然生效）
4. mock 验证 review 拒绝 → 重试路径、模型返回非法 JSON 的容错路径
5. 旧 `--db` 用法回归验证
6. 多文件（如 txt + json）混合查询验证
7. 无 API key / 错误 key 时的报错信息验证

## 不做（YAGNI）

- xlsx 多 sheet 支持（只取第一个非空 sheet）
- 非结构化自由文本问答（需 LLM）
- 结果落盘导出
- 大文件流式处理
