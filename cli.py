from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from core import DataAnalysisAgent, connect_read_only
from openai_model import OpenAIAnalyst


def main() -> None:
    parser = argparse.ArgumentParser(description="Safe read-only NL2SQL Agent (online LLM)")
    parser.add_argument("question")
    data = parser.add_mutually_exclusive_group(required=True)
    data.add_argument("--db", type=Path, help="SQLite database file (read-only native connection)")
    data.add_argument("--file", action="append", type=Path, dest="files", help="data file: .txt/.csv/.xlsx/.xls/.json (repeatable)")
    parser.add_argument("--json", action="store_true", help="print structured trace instead of Markdown report")
    parser.add_argument("--api-key", help="API key (default: env OPENAI_API_KEY)")
    parser.add_argument("--base-url", help="OpenAI-compatible API base URL (default: https://api.openai.com/v1)")
    parser.add_argument("--model", help="model name (default: gpt-4o-mini)")
    parser.add_argument("--timeout", type=float, default=60.0, help="API timeout in seconds (default: 60)")
    parser.add_argument("--env-file", type=Path, help="path to a .env config file (default: ./ .env next to openai_model.py)")
    args = parser.parse_args()

    model = OpenAIAnalyst(api_key=args.api_key, base_url=args.base_url, model=args.model, timeout=args.timeout, env_file=args.env_file)
    if args.db:
        connection = connect_read_only(args.db)
    else:
        from loaders import load_into_memory

        connection = load_into_memory(args.files)
    try:
        result = DataAnalysisAgent(connection, model=model).run(args.question)
    finally:
        connection.close()
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2, default=str) if args.json else result.report)


if __name__ == "__main__":
    main()
