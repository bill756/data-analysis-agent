"""Online LLM analyst backed by any OpenAI-compatible chat/completions endpoint.

Uses only the standard library (urllib), so no extra dependency is required.
Configure with base_url / api_key / model; api_key defaults to the OPENAI_API_KEY
environment variable. Works with OpenAI, DeepSeek, Qwen, Zhipu, local Ollama, etc.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from core import AnalysisPlan, Table

_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_MODEL = "gpt-4o-mini"

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_SQL_BLOCK_RE = re.compile(r"```sql\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_DEFAULT_ENV_FILE = Path(__file__).resolve().parent / ".env"


def _read_env_file(path: Path | None) -> dict[str, str]:
    """Minimal .env parser (KEY=VALUE lines, # comments, optional quotes / export prefix)."""
    config: dict[str, str] = {}
    env_path = path or _DEFAULT_ENV_FILE
    if not env_path.is_file():
        return config
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            config[key] = value
    return config

PLAN_SYSTEM = """你是数据分析规划器。根据用户问题与数据库表结构，选择回答该问题所需的相关表。
规则：
1. relevant_tables 只能包含 schema 中真实存在的表名。
2. 若问题与某张表相关但表名未出现在问题中，也要根据列语义判断并选入。
3. 只输出 JSON，格式：{"goal": "对分析目标的简短描述", "relevant_tables": ["表名", ...]}"""

WRITE_SQL_SYSTEM = """你是只读 SQL 专家。根据用户问题与分析计划，生成 SQLite SELECT 查询。
规则：
1. 只允许单条 SELECT 语句，禁止任何写入/DDL/PRAGMA 操作。
2. 只能引用计划中给出的表名。
3. 列名和表名可能包含中文或空格，引用时必须用双引号，例如 SELECT "部门" FROM "销售数据"。
4. 只输出 SQL 本身（可放在 ```sql 代码块中），不要任何解释。"""

REVIEW_SYSTEM = """你是 SQL 复核员。检查生成的 SQL 是否满足：
1. 是只读 SELECT 查询。
2. 覆盖了分析计划中列出的全部相关表（若计划选了多张表，SQL 应引用其中每张表）。
只输出 JSON，格式：{"approved": true 或 false, "feedback": "若不通过，说明原因；通过则为空字符串"}"""


def _schema_text(schema: tuple[Table, ...]) -> str:
    return "\n".join(
        f"{table.name}({', '.join(f'{column.name}: {column.data_type}' for column in table.columns)})"
        for table in schema
    )


def _extract_json_block(text: str) -> dict[str, Any]:
    """Parse JSON from model output, tolerating code fences and surrounding prose."""
    candidates = [text]
    for block in _JSON_BLOCK_RE.findall(text):
        candidates.append(block.strip())
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        candidates.append(brace.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    raise ValueError(f"模型输出无法解析为 JSON: {text[:200]!r}")


def _extract_sql(text: str) -> str:
    """Extract SQL from model output, tolerating ```sql code fences."""
    block = _SQL_BLOCK_RE.search(text)
    if block:
        return block.group(1).strip()
    return text.strip()


class OpenAIAnalyst:
    """AnalystModel implementation calling an OpenAI-compatible chat/completions API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        env_file: Path | str | None = None,
    ) -> None:
        file_config = _read_env_file(Path(env_file) if env_file else None)
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY") or file_config.get("OPENAI_API_KEY")
        self.base_url = (
            base_url
            or os.environ.get("OPENAI_BASE_URL")
            or file_config.get("OPENAI_BASE_URL")
            or _DEFAULT_BASE_URL
        ).rstrip("/")
        self.model = (
            model or os.environ.get("OPENAI_MODEL") or file_config.get("OPENAI_MODEL") or _DEFAULT_MODEL
        )
        if not self.api_key:
            raise ValueError("缺少 API key：请设置环境变量 OPENAI_API_KEY、.env 文件或传入 --api-key")
        self.timeout = timeout

    def _chat(self, messages: list[dict[str, str]]) -> str:
        body = json.dumps({"model": self.model, "messages": messages, "temperature": 0}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            if exc.code in (401, 403):
                raise RuntimeError(f"API 认证失败（HTTP {exc.code}）：请检查 OPENAI_API_KEY / --api-key。{detail}") from exc
            if exc.code == 404:
                raise RuntimeError(f"接口不存在（HTTP 404）：请检查 --base-url 是否正确。{detail}") from exc
            raise RuntimeError(f"模型服务返回错误（HTTP {exc.code}）：{detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"无法连接模型服务 {self.base_url}：{exc.reason}") from exc
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"模型响应格式异常：{str(payload)[:200]}") from exc

    def plan(self, question: str, schema: tuple[Table, ...]) -> AnalysisPlan:
        messages = [
            {"role": "system", "content": PLAN_SYSTEM},
            {"role": "user", "content": f"数据库表结构：\n{_schema_text(schema)}\n\n用户问题：{question}"},
        ]
        parsed = _extract_json_block(self._chat(messages))
        goal = str(parsed.get("goal", question))
        tables_raw = parsed.get("relevant_tables", [])
        if not isinstance(tables_raw, list) or not tables_raw:
            raise ValueError(f"模型未返回有效的 relevant_tables: {parsed!r}")
        return AnalysisPlan(goal, tuple(str(table) for table in tables_raw))

    def write_sql(self, question: str, plan: AnalysisPlan, schema: tuple[Table, ...], attempt: int, feedback: str) -> str:
        user_parts = [
            f"数据库表结构：\n{_schema_text(schema)}",
            f"用户问题：{question}",
            f"分析计划：目标={plan.goal}；相关表={', '.join(plan.relevant_tables)}",
            f"第 {attempt + 1} 次尝试",
        ]
        if feedback:
            user_parts.append(f"上次尝试被拒绝，原因：{feedback}。请修正后重试。")
        messages = [
            {"role": "system", "content": WRITE_SQL_SYSTEM},
            {"role": "user", "content": "\n".join(user_parts)},
        ]
        return _extract_sql(self._chat(messages))

    def review(self, question: str, plan: AnalysisPlan, sql: str, schema: tuple[Table, ...]) -> tuple[bool, str]:
        messages = [
            {"role": "system", "content": REVIEW_SYSTEM},
            {
                "role": "user",
                "content": f"分析计划：目标={plan.goal}；相关表={', '.join(plan.relevant_tables)}\n\n待复核 SQL：\n{sql}",
            },
        ]
        parsed = _extract_json_block(self._chat(messages))
        approved = bool(parsed.get("approved"))
        return approved, str(parsed.get("feedback", ""))
