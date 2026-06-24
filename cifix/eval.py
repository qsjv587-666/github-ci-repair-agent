from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .run import run_cifix


def run_eval(flags: dict[str, Any]) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    eval_id = f"eval_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    cases_root = Path(flags.get("cases") or "fixtures").resolve()
    out_root = Path(flags.get("out") or "artifacts/eval").resolve()
    eval_dir = out_root / eval_id
    eval_dir.mkdir(parents=True, exist_ok=True)

    variants = eval_variants(flags)
    results = []
    for case in discover_cases(cases_root):
        for variant in variants:
            case_out = eval_dir / "runs" / variant["name"] / case["name"]
            start = time.time()
            result = run_cifix(
                {
                    "repo": str(case["path"]),
                    "command": case["command"],
                    "log": str(case["log"]),
                    "out": str(case_out),
                    "use-model": flags.get("use-model"),
                    "memory-path": flags.get("memory-path") or str(eval_dir / "verified-repairs.json"),
                    "vector-db": flags.get("vector-db"),
                    "embedding-provider": flags.get("embedding-provider"),
                    "embedding-model": flags.get("embedding-model"),
                    "embedding-dimensions": flags.get("embedding-dimensions"),
                    "embedding-base-url": flags.get("embedding-base-url"),
                    "sandbox": flags.get("sandbox"),
                    "docker-image": flags.get("docker-image"),
                    "docker-network": flags.get("docker-network"),
                    **variant["flags"],
                }
            )
            duration_ms = int((time.time() - start) * 1000)
            results.append({"name": case["name"], "variant": variant["name"], "status": result["status"], "passed": result["status"] == "success", "durationMs": duration_ms, "report": result["paths"]["report"], "patch": result["paths"]["patch"], "trace": result["paths"]["trace"]})

    success = len([result for result in results if result["passed"]])
    variant_summary = summarize_variants(results)
    summary = {
        "evalId": eval_id,
        "startedAt": started_at,
        "casesRoot": str(cases_root),
        "caseCount": len(discover_cases(cases_root)),
        "variants": [variant["name"] for variant in variants],
        "total": len(results),
        "success": success,
        "successRate": round(success / len(results), 3) if results else 0,
        "avgDurationMs": int(sum(result["durationMs"] for result in results) / len(results)) if results else 0,
        "modelEnabled": bool(flags.get("use-model")),
        "variantSummary": variant_summary,
        "results": results,
    }
    summary_path = eval_dir / "summary.json"
    report_path = eval_dir / "report.md"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    report_path.write_text(render_eval_report(summary))
    return {
        "evalId": eval_id,
        "caseCount": summary["caseCount"],
        "total": summary["total"],
        "success": success,
        "successRate": summary["successRate"],
        "summaryPath": str(summary_path),
        "reportPath": str(report_path),
    }


def discover_cases(cases_root: Path) -> list[dict[str, Any]]:
    cases = []
    for entry in sorted(cases_root.iterdir()):
        if not entry.is_dir():
            continue
        package_json = entry / "package.json"
        log_path = entry / "ci-fail.log"
        if package_json.exists() and log_path.exists():
            cases.append({"name": entry.name, "path": entry, "log": log_path, "command": "npm test"})
    return cases


def eval_variants(flags: dict[str, Any]) -> list[dict[str, Any]]:
    if not flags.get("compare-baselines"):
        return [{"name": "full", "flags": {}}]
    return [
        {"name": "full", "flags": {}},
        {"name": "no_memory", "flags": {"no-memory": True}},
        {"name": "single_candidate", "flags": {"single-candidate": True}},
    ]


def summarize_variants(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    variants = sorted({result["variant"] for result in results})
    summary = []
    for variant in variants:
        items = [result for result in results if result["variant"] == variant]
        success = len([item for item in items if item["passed"]])
        summary.append(
            {
                "variant": variant,
                "total": len(items),
                "success": success,
                "successRate": round(success / len(items), 3) if items else 0,
                "avgDurationMs": int(sum(item["durationMs"] for item in items) / len(items)) if items else 0,
            }
        )
    return summary


def render_eval_report(summary: dict[str, Any]) -> str:
    variant_rows = "\n".join(f"| {item['variant']} | {item['success']} / {item['total']} | {item['successRate']} | {item['avgDurationMs']} ms |" for item in summary["variantSummary"])
    rows = "\n".join(f"| {result['variant']} | {result['name']} | {result['status']} | {result['durationMs']} ms | {result['report']} |" for result in summary["results"])
    return f"""# CIFix Eval Report

- Eval: {summary['evalId']}
- Started: {summary['startedAt']}
- Cases: {summary['caseCount']}
- Variants: {", ".join(summary['variants'])}
- Total runs: {summary['total']}
- Success: {summary['success']}
- Success rate: {summary['successRate']}
- Average duration: {summary['avgDurationMs']} ms
- Model enabled: {summary['modelEnabled']}

## Variant Summary

| Variant | Success | Success Rate | Avg Duration |
|---|---:|---:|---:|
{variant_rows}

## Case Runs

| Variant | Case | Status | Duration | Report |
|---|---|---|---:|---|
{rows}
"""
