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
            rag_metrics = score_case_rag(case, result)
            results.append(
                {
                    "name": case["name"],
                    "variant": variant["name"],
                    "category": case.get("category"),
                    "difficulty": case.get("difficulty"),
                    "status": result["status"],
                    "passed": result["status"] == "success",
                    "durationMs": duration_ms,
                    "report": result["paths"]["report"],
                    "patch": result["paths"]["patch"],
                    "trace": result["paths"]["trace"],
                    "rag": rag_metrics,
                }
            )

    success = len([result for result in results if result["passed"]])
    variant_summary = summarize_variants(results)
    rag_summary = summarize_rag(results)
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
        "ragSummary": rag_summary,
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
        meta = read_case_meta(entry)
        if package_json.exists() and log_path.exists():
            cases.append({"name": entry.name, "path": entry, "log": log_path, "command": "npm test", **meta})
            continue
        if log_path.exists() and any(path.suffix == ".py" for path in entry.rglob("*.py")):
            cases.append({"name": entry.name, "path": entry, "log": log_path, "command": "python3 -m unittest", **meta})
    return cases


def read_case_meta(entry: Path) -> dict[str, Any]:
    meta_path = entry / "eval-meta.json"
    if not meta_path.exists():
        return {}
    try:
        loaded = json.loads(meta_path.read_text())
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


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


def score_case_rag(case: dict[str, Any], run_result: dict[str, Any]) -> dict[str, Any]:
    expected = case.get("expectedRagIds") or []
    if not expected:
        return {"expected": [], "rank": None, "hitAt1": None, "hitAt3": None, "mrr": None, "topHit": None}
    hits_path = Path(run_result["paths"]["report"]).parent / "repair-playbook-hits.json"
    try:
        hits = json.loads(hits_path.read_text())
    except (OSError, json.JSONDecodeError):
        hits = []
    hit_ids = [hit.get("id") for hit in hits if isinstance(hit, dict)]
    rank = next((index + 1 for index, hit_id in enumerate(hit_ids) if hit_id in expected), None)
    top_hit = hits[0] if hits and isinstance(hits[0], dict) else None
    return {
        "expected": expected,
        "rank": rank,
        "hitAt1": rank == 1,
        "hitAt3": bool(rank and rank <= 3),
        "mrr": round(1 / rank, 3) if rank else 0,
        "topHit": top_hit.get("id") if top_hit else None,
        "topHitSource": top_hit.get("source") if top_hit else None,
        "topHitScore": top_hit.get("hybridScore") if top_hit else None,
    }


def summarize_rag(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    variants = sorted({result["variant"] for result in results})
    summary = []
    for variant in variants:
        items = [result for result in results if result["variant"] == variant and result.get("rag", {}).get("expected")]
        if not items:
            summary.append({"variant": variant, "cases": 0, "hitAt1": 0, "hitAt3": 0, "mrr": 0, "coverage": 0})
            continue
        hit_at_1 = len([item for item in items if item["rag"].get("hitAt1")])
        hit_at_3 = len([item for item in items if item["rag"].get("hitAt3")])
        mrr = sum(float(item["rag"].get("mrr") or 0) for item in items) / len(items)
        coverage = len([item for item in items if item["rag"].get("rank")]) / len(items)
        summary.append(
            {
                "variant": variant,
                "cases": len(items),
                "hitAt1": round(hit_at_1 / len(items), 3),
                "hitAt3": round(hit_at_3 / len(items), 3),
                "mrr": round(mrr, 3),
                "coverage": round(coverage, 3),
            }
        )
    return summary


def render_eval_report(summary: dict[str, Any]) -> str:
    variant_rows = "\n".join(f"| {item['variant']} | {item['success']} / {item['total']} | {item['successRate']} | {item['avgDurationMs']} ms |" for item in summary["variantSummary"])
    rag_rows = "\n".join(f"| {item['variant']} | {item['cases']} | {item['hitAt1']} | {item['hitAt3']} | {item['mrr']} | {item['coverage']} |" for item in summary.get("ragSummary", []))
    rows = "\n".join(
        f"| {result['variant']} | {result['name']} | {result.get('category') or 'n/a'} | {result['status']} | {result['durationMs']} ms | {format_rag_cell(result.get('rag', {}))} | {result['report']} |"
        for result in summary["results"]
    )
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

## RAG Evidence Metrics

| Variant | Cases with Expected Evidence | Hit@1 | Hit@3 | MRR | Coverage |
|---|---:|---:|---:|---:|---:|
{rag_rows}

Metric definitions:

- Hit@1: the expected repair evidence is ranked first.
- Hit@3: the expected repair evidence appears in the top 3 retrieved items.
- MRR: mean reciprocal rank of the first expected evidence item.
- Coverage: percentage of cases where at least one expected evidence item is retrieved.

## Case Runs

| Variant | Case | Category | Status | Duration | RAG | Report |
|---|---|---|---|---:|---|---|
{rows}
"""


def format_rag_cell(rag: dict[str, Any]) -> str:
    if not rag.get("expected"):
        return "n/a"
    rank = rag.get("rank") or "miss"
    top = rag.get("topHit") or "none"
    return f"rank={rank}; top={top}; hit@3={rag.get('hitAt3')}"
