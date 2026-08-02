"""End-to-end tests: run the CLI against the local mock LLM server (no real API key needed).

Usage: python scripts/e2e_test.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from http.server import HTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
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


def run_cli(*args: str) -> dict:
    result = subprocess.run(
        [sys.executable, str(ROOT / "cli.py"), *args, "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
    )
    if result.returncode != 0:
        return {"status": f"cli-crash({result.returncode})", "report": result.stderr or result.stdout}
    return json.loads(result.stdout)


def start_mock(mode: str) -> tuple[HTTPServer, str]:
    Handler.mode = mode
    Handler.review_calls = 0
    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}/v1"


def main() -> None:
    server, base_url = start_mock("normal")

    print("[1] 五种格式逐一查询")
    for ext in ("csv", "txt", "xlsx", "xls", "json"):
        result = run_cli("各部门销售额汇总", "--file", str(EXAMPLES / f"sales.{ext}"), "--base-url", base_url, "--api-key", "test")
        check(f"{ext}: status=completed", result.get("status") == "completed", str(result)[:200])
        check(f"{ext}: 报告含研发", "研发" in result.get("report", ""), result.get("report", "")[:100])

    print("[2] --json 轨迹完整性")
    result = run_cli("各部门销售额汇总", "--file", str(EXAMPLES / "sales.json"), "--base-url", base_url, "--api-key", "test")
    steps = [step.get("step") for step in result.get("trace", [])]
    check("trace 六步齐全", steps == ["inspect_schema", "plan", "write_sql", "guard", "review", "execute", "report"], str(steps))

    print("[3] 多文件混合 (txt + json)")
    result = run_cli("各部门销售额汇总", "--file", str(EXAMPLES / "sales.txt"), "--file", str(EXAMPLES / "sales.json"), "--base-url", base_url, "--api-key", "test")
    check("混合查询 completed", result.get("status") == "completed", str(result)[:200])

    print("[4] 旧 --db 回归")
    result = run_cli("各部门销售额汇总", "--db", str(ROOT / "sales.db"), "--base-url", base_url, "--api-key", "test")
    check("--db completed", result.get("status") == "completed", str(result)[:200])

    server.shutdown()

    print("[5] 恶意 SQL 拦截 (evil_sql)")
    server, base_url = start_mock("evil_sql")
    result = run_cli("各部门销售额汇总", "--file", str(EXAMPLES / "sales.xlsx"), "--base-url", base_url, "--api-key", "test")
    check("rejected", result.get("status") == "rejected", str(result)[:200])
    check("拒绝原因含安全校验", "安全校验失败" in result.get("report", ""), result.get("report", "")[:200])
    server.shutdown()

    print("[6] review 拒绝后重试 (reject_once)")
    server, base_url = start_mock("reject_once")
    result = run_cli("各部门销售额汇总", "--file", str(EXAMPLES / "sales.xlsx"), "--base-url", base_url, "--api-key", "test")
    check("重试后 completed", result.get("status") == "completed", str(result)[:200])
    attempts = [step for step in result.get("trace", []) if step.get("step") == "write_sql"]
    check("确实重试了两次", len(attempts) == 2, str(attempts))
    server.shutdown()

    print("[7] 模型返回非法 JSON (bad_json)")
    server, base_url = start_mock("bad_json")
    result = run_cli("各部门销售额汇总", "--file", str(EXAMPLES / "sales.json"), "--base-url", base_url, "--api-key", "test")
    check("error 状态", result.get("status") == "error", str(result)[:200])
    check("报错含模型规划失败", "模型规划失败" in result.get("report", ""), result.get("report", "")[:200])
    server.shutdown()

    print("[8] API 认证错误提示 (auth_error)")
    server, base_url = start_mock("auth_error")
    result = run_cli("各部门销售额汇总", "--db", str(ROOT / "sales.db"), "--base-url", base_url, "--api-key", "bad-key")
    check("error 状态", result.get("status") == "error", str(result)[:200])
    check("报错含认证提示", "认证失败" in result.get("report", ""), result.get("report", "")[:200])
    server.shutdown()

    print("[9] 无 API key 启动报错")
    env = {"PATH": "IGNORED"}  # ensure no OPENAI_API_KEY
    import os

    clean_env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    result = subprocess.run(
        [sys.executable, str(ROOT / "cli.py"), "test", "--db", str(ROOT / "sales.db"), "--env-file", str(ROOT / "no_such_file.env")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        env=clean_env,
    )
    check("无 key 报错提示", "OPENAI_API_KEY" in (result.stderr or result.stdout), (result.stderr or result.stdout)[:200])

    print("[10] 行数截断")
    with tempfile.TemporaryDirectory() as tmp:
        big = Path(tmp) / "sales.txt"
        big.write_text("部门\t金额\n" + "".join(f"部门{i % 3}\t{i}\n" for i in range(105)), encoding="utf-8")
        server, base_url = start_mock("normal")
        result = run_cli("列出所有数据", "--file", str(big), "--base-url", base_url, "--api-key", "test")
        check("截断提示", "仅展示前 100 行" in result.get("report", ""), result.get("report", "")[:200])
        check("rows=100", len(result.get("rows", [])) == 100, str(len(result.get("rows", []))))
        server.shutdown()

    print("[11] .env 文件加载（无环境变量、无命令行参数）")
    with tempfile.TemporaryDirectory() as tmp:
        server, base_url = start_mock("normal")
        env_file = Path(tmp) / "config.env"
        env_file.write_text(
            f"# test config\nOPENAI_API_KEY=key-from-env\nOPENAI_BASE_URL={base_url}\nOPENAI_MODEL=mock-model\n",
            encoding="utf-8",
        )
        clean_env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")}
        result = subprocess.run(
            [sys.executable, str(ROOT / "cli.py"), "各部门销售额汇总", "--file", str(EXAMPLES / "sales.json"), "--env-file", str(env_file), "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=ROOT,
            env=clean_env,
        )
        check(".env 加载后 completed", result.returncode == 0 and '"status": "completed"' in result.stdout, (result.stdout + result.stderr)[:300])
        server.shutdown()

    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
