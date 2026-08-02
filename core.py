from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


FORBIDDEN = {"insert", "update", "delete", "drop", "alter", "create", "replace", "attach", "detach", "pragma", "vacuum", "reindex"}
TABLE_RE = re.compile(r'\b(?:from|join)\s+(?:"((?:[^"]|"")*)"|([A-Za-z_][A-Za-z0-9_]*))', re.IGNORECASE)
WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
STRING_RE = re.compile(r'"(?:[^"]|"")*"')


@dataclass(frozen=True)
class Column:
    name: str
    data_type: str


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]


def inspect_schema(connection: sqlite3.Connection) -> tuple[Table, ...]:
    names = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    tables: list[Table] = []
    for (name,) in names:
        escaped = name.replace('"', '""')
        columns = connection.execute(f'PRAGMA table_info("{escaped}")').fetchall()
        tables.append(Table(name, tuple(Column(row[1], row[2]) for row in columns)))
    return tuple(tables)


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    reason: str
    tables: tuple[str, ...] = ()


class SqlGuard:
    def validate(self, sql: str, schema: tuple[Table, ...]) -> GuardResult:
        stripped = sql.strip()
        statements = [part for part in stripped.split(";") if part.strip()]
        if len(statements) != 1:
            return GuardResult(False, "only one SQL statement is allowed")
        normalized = statements[0].strip()
        first = WORD_RE.search(normalized)
        if first is None or first.group(0).lower() not in {"select", "with"}:
            return GuardResult(False, "only SELECT queries are allowed")
        words = {word.lower() for word in WORD_RE.findall(STRING_RE.sub("", normalized))}
        blocked = sorted(words & FORBIDDEN)
        if blocked:
            return GuardResult(False, f"forbidden SQL keyword: {blocked[0]}")
        referenced = tuple(dict.fromkeys((match.group(1) or match.group(2)).replace('""', '"') for match in TABLE_RE.finditer(normalized)))
        known = {table.name.lower() for table in schema}
        unknown = [name for name in referenced if name.lower() not in known]
        if unknown:
            return GuardResult(False, f"unknown table: {unknown[0]}")
        if not referenced:
            return GuardResult(False, "query must reference a known table")
        return GuardResult(True, "read-only query accepted", referenced)


@dataclass(frozen=True)
class AnalysisPlan:
    goal: str
    relevant_tables: tuple[str, ...]


class AnalystModel(Protocol):
    def plan(self, question: str, schema: tuple[Table, ...]) -> AnalysisPlan: ...

    def write_sql(self, question: str, plan: AnalysisPlan, schema: tuple[Table, ...], attempt: int, feedback: str) -> str: ...

    def review(self, question: str, plan: AnalysisPlan, sql: str, schema: tuple[Table, ...]) -> tuple[bool, str]: ...




@dataclass
class AnalysisResult:
    status: str
    sql: str | None
    columns: list[str]
    rows: list[tuple[object, ...]]
    report: str
    trace: list[dict[str, object]]


class DataAnalysisAgent:
    def __init__(self, connection: sqlite3.Connection, model: AnalystModel | None = None, row_limit: int = 100, max_revisions: int = 1) -> None:
        self.connection = connection
        if model is None:
            from openai_model import OpenAIAnalyst  # deferred import avoids circular dependency

            model = OpenAIAnalyst()
        self.model = model
        self.row_limit = max(1, row_limit)
        self.max_revisions = max(0, max_revisions)
        self.guard = SqlGuard()

    def _execute_read_only(self, sql: str) -> tuple[list[str], list[tuple[object, ...]], bool]:
        allowed_actions = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}

        def authorizer(action, arg1, arg2, database, source):
            return sqlite3.SQLITE_OK if action in allowed_actions else sqlite3.SQLITE_DENY

        steps = 0

        def progress() -> int:
            nonlocal steps
            steps += 1
            return 1 if steps > 10000 else 0

        self.connection.set_authorizer(authorizer)
        self.connection.set_progress_handler(progress, 100)
        try:
            cursor = self.connection.execute(sql)
            columns = [item[0] for item in cursor.description or ()]
            fetched = cursor.fetchmany(self.row_limit + 1)
            return columns, fetched[: self.row_limit], len(fetched) > self.row_limit
        finally:
            self.connection.set_authorizer(None)
            self.connection.set_progress_handler(None, 0)

    def _report(self, question: str, sql: str, columns: list[str], rows: list[tuple[object, ...]], truncated: bool) -> str:
        lines = ["# 数据分析报告", "", f"**问题：** {question}", "", "## 结果"]
        if not rows:
            lines.extend(["", "查询未返回数据，无法据此得出结论。"])
        else:
            lines.extend(["", "| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"])
            lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
        lines.extend(["", "## 执行 SQL", "", "```sql", sql, "```"])
        if truncated:
            lines.extend(["", f"> 结果超过 {self.row_limit} 行，报告仅展示前 {self.row_limit} 行。"])
        return "\n".join(lines)

    def run(self, question: str) -> AnalysisResult:
        trace: list[dict[str, object]] = []
        schema = inspect_schema(self.connection)
        trace.append({"step": "inspect_schema", "tables": [table.name for table in schema]})
        if not schema:
            return AnalysisResult("error", None, [], [], "数据库中没有可分析的业务表。", trace)
        try:
            plan = self.model.plan(question, schema)
        except Exception as exc:
            trace.append({"step": "plan", "error": str(exc)})
            return AnalysisResult("error", None, [], [], f"模型规划失败：{exc}", trace)
        trace.append({"step": "plan", "tables": list(plan.relevant_tables)})
        feedback = ""
        for attempt in range(self.max_revisions + 1):
            try:
                sql = self.model.write_sql(question, plan, schema, attempt, feedback)
            except Exception as exc:
                trace.append({"step": "write_sql", "attempt": attempt + 1, "error": str(exc)})
                return AnalysisResult("error", None, [], [], f"模型生成 SQL 失败：{exc}", trace)
            trace.append({"step": "write_sql", "attempt": attempt + 1, "sql": sql})
            guard = self.guard.validate(sql, schema)
            trace.append({"step": "guard", "allowed": guard.allowed, "reason": guard.reason})
            if not guard.allowed:
                feedback = guard.reason
                if attempt < self.max_revisions:
                    continue
                return AnalysisResult("rejected", sql, [], [], f"SQL 安全校验失败：{guard.reason}", trace)
            try:
                approved, feedback = self.model.review(question, plan, sql, schema)
            except Exception as exc:
                trace.append({"step": "review", "error": str(exc)})
                return AnalysisResult("error", None, [], [], f"模型复核失败：{exc}", trace)
            trace.append({"step": "review", "approved": approved, "feedback": feedback})
            if not approved:
                if attempt < self.max_revisions:
                    continue
                return AnalysisResult("rejected", sql, [], [], f"SQL 复核未通过：{feedback}", trace)
            try:
                columns, rows, truncated = self._execute_read_only(sql)
            except sqlite3.Error as exc:
                trace.append({"step": "execute", "ok": False, "error": str(exc)})
                return AnalysisResult("error", sql, [], [], f"SQL 执行失败：{exc}", trace)
            trace.append({"step": "execute", "ok": True, "rows": len(rows), "truncated": truncated})
            report = self._report(question, sql, columns, rows, truncated)
            trace.append({"step": "report", "status": "completed"})
            return AnalysisResult("completed", sql, columns, rows, report, trace)
        raise AssertionError("unreachable")


def connect_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{Path(path).resolve().as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)
