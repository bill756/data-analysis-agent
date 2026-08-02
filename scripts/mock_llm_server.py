"""Local mock OpenAI-compatible chat/completions server for offline end-to-end testing.

Usage:
    python scripts/mock_llm_server.py [--port 8765] [--mode normal]

Modes:
    normal       - well-formed plan / SQL / review responses
    evil_sql     - write_sql returns DROP TABLE (tests the SqlGuard rejection path)
    reject_once  - first review rejects, second approves (tests the retry path)
    bad_json     - plan returns non-JSON text (tests the parse-error path)
    auth_error   - every request answers HTTP 401 (tests the auth error message)

Point the agent at it with: python cli.py "..." --file examples/sales.xlsx --base-url http://127.0.0.1:8765/v1 --api-key test-key
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

PLAN_SYSTEM_MARK = "数据分析规划器"
WRITE_SQL_SYSTEM_MARK = "只读 SQL 专家"
REVIEW_SYSTEM_MARK = "SQL 复核员"

PLAN_RESPONSE = json.dumps({"goal": "汇总各部门销售额", "relevant_tables": ["sales"]}, ensure_ascii=False)
SQL_RESPONSE = 'SELECT "部门", ROUND(SUM("金额"), 2) AS "总金额" FROM "sales" GROUP BY "部门" ORDER BY "总金额" DESC'
REVIEW_OK = json.dumps({"approved": True, "feedback": ""}, ensure_ascii=False)
REVIEW_REJECT = json.dumps({"approved": False, "feedback": "SQL 未覆盖计划中的全部相关表"}, ensure_ascii=False)


class Handler(BaseHTTPRequestHandler):
    mode = "normal"
    review_calls = 0

    def do_POST(self) -> None:
        if self.mode == "auth_error":
            self._reply(401, {"error": {"message": "Invalid API key provided", "type": "invalid_request_error"}})
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages", [{}])
        system = messages[0].get("content", "")
        user_text = messages[-1].get("content", "") if len(messages) > 1 else ""
        if PLAN_SYSTEM_MARK in system:
            content = "不是有效的 JSON 内容" if self.mode == "bad_json" else PLAN_RESPONSE
        elif WRITE_SQL_SYSTEM_MARK in system:
            question_match = re.search(r"用户问题：(.*?)(?:\n|$)", user_text)
            question = question_match.group(1) if question_match else user_text
            if self.mode == "evil_sql":
                content = 'DROP TABLE "sales"'
            elif "汇总" in question:
                content = SQL_RESPONSE
            else:
                content = 'SELECT * FROM "sales"'
        elif REVIEW_SYSTEM_MARK in system:
            if self.mode == "reject_once" and Handler.review_calls == 0:
                Handler.review_calls += 1
                content = REVIEW_REJECT
            else:
                content = REVIEW_OK
        else:
            content = "unrecognized prompt"
        self._reply(200, {"choices": [{"message": {"content": content}}]})

    def _reply(self, code: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:  # silence request logging
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--mode", default="normal", choices=["normal", "evil_sql", "reject_once", "bad_json", "auth_error"])
    args = parser.parse_args()
    Handler.mode = args.mode
    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"mock LLM server (mode={args.mode}) listening on http://127.0.0.1:{args.port}/v1/chat/completions", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
