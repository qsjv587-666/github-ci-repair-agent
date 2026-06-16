#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from .dashboard import generate_dashboard
from .eval import run_eval
from .inspect import inspect_github
from .model import model_config_from_env
from .rag import embedding_config_from_flags, query_repair_rag
from .run import run_cifix


def main(argv: list[str] | None = None) -> int:
    load_dot_env(Path(".env"))
    command, flags = parse_args(argv or sys.argv[1:])
    if not command or command == "help" or flags.get("help"):
        print_help()
        return 0
    if command == "doctor":
        print_doctor()
        return 0
    if command == "eval":
        result = run_eval(flags)
        print(f"eval_id: {result['evalId']}")
        print(f"cases: {result['caseCount']}")
        print(f"total_runs: {result['total']}")
        print(f"success: {result['success']}")
        print(f"success_rate: {result['successRate']}")
        print(f"report: {result['reportPath']}")
        return 0
    if command == "inspect":
        result = inspect_github(flags)
        print(f"inspect_id: {result['inspectId']}")
        print(f"context: {result['paths']['context']}")
        print(f"log: {result['paths']['log']}")
        print(f"report: {result['paths']['report']}")
        return 0
    if command == "dashboard":
        result = generate_dashboard(flags)
        print(f"dashboard: {result['dashboardPath']}")
        print(f"runs: {result['runs']}")
        print(f"evals: {result['evals']}")
        print(f"inspections: {result['inspections']}")
        return 0
    if command == "rag":
        result = query_repair_rag(flags)
        print(f"documents: {result['stats']['documents']}")
        print(f"index: {result['stats']['indexPath']}")
        print(f"vector_backend: {result['stats']['vectorBackend']}")
        print(f"vector_db_path: {result['stats']['vectorDbPath']}")
        for index, hit in enumerate(result["hits"], start=1):
            print(
                f"{index}. {hit['id']} source={hit['source']} "
                f"hybrid={hit['hybridScore']} bm25={hit['bm25Score']} vector={hit['vectorScore']}"
            )
            print(f"   {hit['strategy']}")
        return 0
    if command != "run":
        raise ValueError(f"Unknown command: {command}")
    result = run_cifix(flags)
    print(f"run_id: {result['runId']}")
    print(f"status: {result['status']}")
    print(f"recommended_patch: {result['paths']['patch']}")
    print(f"report: {result['paths']['report']}")
    print(f"trace: {result['paths']['trace']}")
    print(f"pr_comment_draft: {result['paths']['prComment']}")
    return 0


def parse_args(argv: list[str]) -> tuple[str | None, dict[str, Any]]:
    if not argv:
        return None, {}
    command, rest = argv[0], argv[1:]
    flags: dict[str, Any] = {"_": []}
    index = 0
    while index < len(rest):
        arg = rest[index]
        if not arg.startswith("--"):
            flags["_"].append(arg)
            index += 1
            continue
        key = arg[2:]
        if index + 1 >= len(rest) or rest[index + 1].startswith("--"):
            flags[key] = True
            index += 1
            continue
        flags[key] = rest[index + 1]
        index += 2
    return command, flags


def print_help() -> None:
    print("""CIFix Agent MVP

Usage:
  cifix doctor
  cifix inspect --url https://github.com/owner/repo/pull/123 [--token-env GITHUB_TOKEN]
  cifix rag --query "ERR_ASSERTION disabled false true" [--memory-path artifacts/memory/verified-repairs.json] [--vector-db sqlite|chroma] [--embedding-provider hash|dashscope|zhipu]
  cifix dashboard [--artifacts artifacts] [--out artifacts/dashboard/index.html]
  cifix eval --cases fixtures [--out artifacts/eval] [--use-model] [--compare-baselines]
  cifix run --repo <path> --command "npm test" --log <ci-log> [--out artifacts] [--setup-command "npm ci"] [--memory-path artifacts/memory/verified-repairs.json]
  cifix run --url https://github.com/owner/repo/pull/123 --token-env GITHUB_TOKEN
  cifix run --url https://github.com/owner/repo/actions/runs/456/job/789 --token-env GITHUB_TOKEN
  cifix run --repo owner/repo --pr 123 --run-id <id> --job <id> --token-env GITHUB_TOKEN

Model mode:
  POE_API_KEY=... POE_MODEL=Claude-Opus-4.6 python -m cifix.cli run --repo <path> --command "npm test" --log <ci-log> --use-model
""")


def print_doctor() -> None:
    config = model_config_from_env({"use-model": True})
    embedding_config = embedding_config_from_flags({})
    print("CIFix Agent doctor")
    print(f"POE_API_KEY: {'set' if config.get('apiKey') else 'missing'}")
    print(f"POE_BASE_URL: {config['baseUrl']}")
    print(f"POE_MODEL: {config['model']}")
    print("Model mode default: disabled unless --use-model or CIFIX_USE_MODEL=1 is set")
    print(f"EMBEDDING_PROVIDER: {embedding_config['provider']}")
    print(f"EMBEDDING_MODEL: {embedding_config['model']}")
    print(f"EMBEDDING_DIMENSIONS: {embedding_config['dimensions']}")
    print(f"DASHSCOPE_API_KEY: {'set' if os.getenv('DASHSCOPE_API_KEY') else 'missing'}")
    print(f"ZHIPU_API_KEY: {'set' if os.getenv('ZHIPU_API_KEY') or os.getenv('ZAI_API_KEY') else 'missing'}")
    print("GitHub token env example: --token-env GITHUB_TOKEN")


def load_dot_env(file_path: Path) -> None:
    if not file_path.exists():
        return
    for line in file_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
