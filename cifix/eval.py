from __future__ import annotations

import json
import math
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
    cases = discover_cases(cases_root)
    for case in cases:
        for variant in variants:
            case_out = eval_dir / "runs" / variant["name"] / case["name"]
            memory_path = memory_path_for_case(eval_dir=eval_dir, case=case, variant=variant, flags=flags)
            start = time.time()
            result = run_cifix(
                {
                    "repo": str(case["path"]),
                    "command": case["command"],
                    "log": str(case["log"]),
                    "out": str(case_out),
                    "use-model": flags.get("use-model"),
                    "memory-path": str(memory_path),
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
        "caseCount": len(cases),
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
    if flags.get("rag-eval-modes"):
        return [
            {"name": "rag_cold_start", "flags": {}, "memoryMode": "cold_start"},
            {"name": "rag_warm_start", "flags": {}, "memoryMode": "warm_start"},
        ]
    if not flags.get("compare-baselines"):
        return [{"name": "full", "flags": {}, "memoryMode": "default"}]
    return [
        {"name": "full", "flags": {}, "memoryMode": "default"},
        {"name": "no_memory", "flags": {"no-memory": True}, "memoryMode": "default"},
        {"name": "single_candidate", "flags": {"single-candidate": True}, "memoryMode": "default"},
    ]


def memory_path_for_case(*, eval_dir: Path, case: dict[str, Any], variant: dict[str, Any], flags: dict[str, Any]) -> Path:
    mode = variant.get("memoryMode", "default")
    if mode == "cold_start":
        return eval_dir / "case-memory" / variant["name"] / case["name"] / "verified-repairs.json"
    if mode == "warm_start":
        memory_path = eval_dir / "case-memory" / variant["name"] / case["name"] / "verified-repairs.json"
        write_filtered_warm_memory(source_path=base_memory_path(flags, eval_dir), target_path=memory_path, case=case)
        return memory_path
    return Path(flags.get("memory-path") or eval_dir / "verified-repairs.json").resolve()


def base_memory_path(flags: dict[str, Any], eval_dir: Path) -> Path:
    if flags.get("memory-path"):
        return Path(flags["memory-path"]).resolve()
    default_memory = Path("artifacts/memory/verified-repairs.json").resolve()
    return default_memory if default_memory.exists() else eval_dir / "verified-repairs.json"


def write_filtered_warm_memory(*, source_path: Path, target_path: Path, case: dict[str, Any]) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    repairs = load_repairs(source_path)
    filtered = [repair for repair in repairs if not is_probable_self_repair(repair, case)]
    target_path.write_text(json.dumps({"repairs": filtered}, ensure_ascii=False, indent=2) + "\n")


def load_repairs(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        loaded = json.loads(path.read_text())
    except json.JSONDecodeError:
        return []
    if isinstance(loaded, dict):
        loaded = loaded.get("repairs", [])
    return loaded if isinstance(loaded, list) else []


def is_probable_self_repair(repair: dict[str, Any], case: dict[str, Any]) -> bool:
    expected_files = set(case.get("expectedChangedFiles") or [])
    changed_files = set((repair.get("patchSummary") or {}).get("changedFiles") or [])
    if expected_files and changed_files == expected_files:
        return True
    case_name = case.get("name", "")
    return bool(case_name and case_name in str(repair.get("id", "")))


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
    hits_path = Path(run_result["paths"]["report"]).parent / "repair-playbook-hits.json"
    try:
        hits = json.loads(hits_path.read_text())
    except (OSError, json.JSONDecodeError):
        hits = []
    if not isinstance(hits, list):
        hits = []
    legacy = score_legacy_rag_ids(expected, hits)
    relevance = score_relevance_rag(case, hits)
    return {
        "legacy": legacy,
        "relevance": relevance,
        "expected": legacy["expected"],
        "rank": legacy["rank"],
        "hitAt1": legacy["hitAt1"],
        "hitAt3": legacy["hitAt3"],
        "mrr": legacy["mrr"],
        "topHit": relevance["topHit"],
        "topHitSource": relevance["topHitSource"],
        "topHitScore": relevance["topHitScore"],
    }


def score_legacy_rag_ids(expected: list[str], hits: list[dict[str, Any]]) -> dict[str, Any]:
    if not expected:
        return {"expected": [], "rank": None, "hitAt1": None, "hitAt3": None, "mrr": None, "topHit": None}
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


def score_relevance_rag(case: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    profile = relevance_profile_for_case(case)
    grades = [grade_hit_relevance(case, hit, profile) for hit in hits[:5] if isinstance(hit, dict)]
    best_rank = next((index + 1 for index, grade in enumerate(grades) if grade >= 2), None)
    top_hit = hits[0] if hits and isinstance(hits[0], dict) else None
    return {
        "profile": profile,
        "grades": grades,
        "bestRank": best_rank,
        "recallAt5": any(grade >= 2 for grade in grades),
        "usefulAt3": any(grade >= 2 for grade in grades[:3]),
        "mrr": round(1 / best_rank, 3) if best_rank else 0,
        "ndcgAt5": round(ndcg_at_k(grades, 5), 3),
        "topHit": top_hit.get("id") if top_hit else None,
        "topHitSource": top_hit.get("source") if top_hit else None,
        "topHitScore": top_hit.get("hybridScore") if top_hit else None,
    }


def relevance_profile_for_case(case: dict[str, Any]) -> dict[str, Any]:
    explicit = case.get("expectedEvidence") or {}
    category = case.get("category")
    defaults = CATEGORY_RELEVANCE_PROFILES.get(category, {})
    return {
        "rootCause": explicit.get("rootCause") or defaults.get("rootCause") or category or "unknown",
        "concepts": list(dict.fromkeys([*defaults.get("concepts", []), *explicit.get("concepts", [])])),
        "strategies": list(dict.fromkeys([*defaults.get("strategies", []), *explicit.get("strategies", [])])),
        "failureTypes": explicit.get("failureTypes") or defaults.get("failureTypes") or [],
        "errorCodes": explicit.get("errorCodes") or defaults.get("errorCodes") or [],
        "changedFiles": explicit.get("changedFiles") or case.get("expectedChangedFiles") or [],
        "acceptedSources": explicit.get("acceptedSources") or ["static-playbook", "verified-repair"],
    }


CATEGORY_RELEVANCE_PROFILES: dict[str, dict[str, Any]] = {
    "python_assertion": {
        "rootCause": "incorrect arithmetic source behavior",
        "concepts": ["add", "sum", "subtract", "calculator"],
        "strategies": ["return the sum", "fix source contract"],
        "failureTypes": ["test_assertion_failure"],
        "errorCodes": ["ASSERTION"],
    },
    "python_business_rule": {
        "rootCause": "business rule constant mismatch",
        "concepts": ["discount", "enterprise", "rate", "percent"],
        "strategies": ["apply configured rate", "fix source contract"],
        "failureTypes": ["test_assertion_failure"],
        "errorCodes": ["ASSERTION"],
    },
    "python_contract_mismatch": {
        "rootCause": "field contract mismatch",
        "concepts": ["profile", "field", "full_name", "serializer", "public api", "contract"],
        "strategies": ["use exposed field", "match public api field", "align consumer with provider"],
        "failureTypes": ["runtime_error", "test_assertion_failure"],
        "errorCodes": ["KeyError", "ASSERTION"],
    },
    "python_import_refactor": {
        "rootCause": "module path changed after refactor",
        "concepts": ["import", "module", "refactor", "date parser", "time_utils"],
        "strategies": ["update call sites", "new module path"],
        "failureTypes": ["import_error"],
        "errorCodes": ["ModuleNotFoundError", "ImportError"],
    },
    "python_none_guard": {
        "rootCause": "optional input missing guard",
        "concepts": ["none", "optional", "name", "formatting", "attributeerror"],
        "strategies": ["handle optional names", "add guard", "without throwing"],
        "failureTypes": ["runtime_error"],
        "errorCodes": ["AttributeError"],
    },
    "python_missing_data": {
        "rootCause": "missing data default",
        "concepts": ["missing", "key", "total", "cart", "default"],
        "strategies": ["default missing totals", "safe default", "missing optional data"],
        "failureTypes": ["runtime_error"],
        "errorCodes": ["KeyError"],
    },
    "python_filter_logic": {
        "rootCause": "filter condition missing",
        "concepts": ["active", "filter", "users", "inactive"],
        "strategies": ["filter inactive users", "source contract"],
        "failureTypes": ["test_assertion_failure"],
        "errorCodes": ["ASSERTION"],
    },
    "python_config_unit": {
        "rootCause": "configuration unit mismatch",
        "concepts": ["timeout", "seconds", "milliseconds", "config"],
        "strategies": ["convert seconds to milliseconds", "runtime config"],
        "failureTypes": ["test_assertion_failure"],
        "errorCodes": ["ASSERTION"],
    },
    "python_value_error": {
        "rootCause": "date format mismatch",
        "concepts": ["date", "format", "iso", "parse"],
        "strategies": ["match iso date format", "date parsing"],
        "failureTypes": ["runtime_error"],
        "errorCodes": ["ValueError"],
    },
    "python_env_default": {
        "rootCause": "missing environment default",
        "concepts": ["settings", "environment", "default", "app_mode"],
        "strategies": ["safe default", "environment variable is absent"],
        "failureTypes": ["runtime_error"],
        "errorCodes": ["KeyError"],
    },
    "python_aggregation_logic": {
        "rootCause": "aggregation omits required field",
        "concepts": ["order", "total", "tax", "subtotal"],
        "strategies": ["include tax", "order totals"],
        "failureTypes": ["test_assertion_failure"],
        "errorCodes": ["ASSERTION"],
    },
    "python_permission_logic": {
        "rootCause": "role permission condition incomplete",
        "concepts": ["owner", "admin", "role", "permission", "access"],
        "strategies": ["owner role", "same access as admin"],
        "failureTypes": ["test_assertion_failure"],
        "errorCodes": ["ASSERTION"],
    },
    "python_pagination_logic": {
        "rootCause": "pagination offset off by one",
        "concepts": ["pagination", "page", "one-based", "offset"],
        "strategies": ["one-based", "page numbers"],
        "failureTypes": ["test_assertion_failure"],
        "errorCodes": ["ASSERTION"],
    },
    "python_multifile_pipeline": {
        "rootCause": "pipeline missing normalization step",
        "concepts": ["pipeline", "normalize", "service data", "transformation"],
        "strategies": ["normalize service data", "before transformation"],
        "failureTypes": ["test_assertion_failure"],
        "errorCodes": ["ASSERTION"],
    },
}


def grade_hit_relevance(case: dict[str, Any], hit: dict[str, Any], profile: dict[str, Any]) -> int:
    text = hit_text(hit)
    exact_id = hit.get("id") in (case.get("expectedRagIds") or [])
    source_ok = hit.get("source") in profile.get("acceptedSources", [])
    failure_ok = not profile.get("failureTypes") or hit.get("failureType") in profile.get("failureTypes", [])
    error_ok = not profile.get("errorCodes") or hit.get("errorCode") in profile.get("errorCodes", [])
    file_ok = any(path in (hit.get("changedFiles") or []) or Path(path).name.lower() in text for path in profile.get("changedFiles", []))
    concept_hits = count_phrase_hits(profile.get("concepts", []), text)
    strategy_hits = count_phrase_hits(profile.get("strategies", []), text)
    if exact_id:
        return 3
    if source_ok and file_ok and (concept_hits or strategy_hits):
        return 3
    if source_ok and strategy_hits >= 2:
        return 3
    if source_ok and failure_ok and error_ok and (concept_hits >= 2 or strategy_hits >= 1 or file_ok):
        return 2
    if source_ok and (failure_ok or error_ok) and (concept_hits >= 1 or strategy_hits >= 1):
        return 2
    if source_ok and failure_ok and error_ok:
        return 1
    if concept_hits or strategy_hits:
        return 1
    return 0


def hit_text(hit: dict[str, Any]) -> str:
    parts = [
        hit.get("id"),
        hit.get("source"),
        hit.get("failureSignature"),
        hit.get("failureType"),
        hit.get("errorCode"),
        hit.get("language"),
        hit.get("strategy"),
        " ".join(hit.get("changedFiles") or []),
        " ".join(hit.get("changedFilePatterns") or []),
    ]
    return normalize_text(" ".join(str(part or "") for part in parts))


def count_phrase_hits(phrases: list[str], text: str) -> int:
    return sum(1 for phrase in phrases if normalize_text(phrase) in text)


def normalize_text(value: str) -> str:
    return value.lower().replace("_", " ").replace("-", " ")


def ndcg_at_k(grades: list[int], k: int) -> float:
    actual = dcg_at_k(grades[:k])
    ideal = dcg_at_k(sorted(grades, reverse=True)[:k])
    return actual / ideal if ideal else 0.0


def dcg_at_k(grades: list[int]) -> float:
    return sum(((2**grade - 1) / math.log2(index + 2)) for index, grade in enumerate(grades))


def summarize_rag(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    variants = sorted({result["variant"] for result in results})
    summary = []
    for variant in variants:
        items = [result for result in results if result["variant"] == variant and result.get("rag", {}).get("relevance")]
        if not items:
            summary.append({"variant": variant, "cases": 0, "recallAt5": 0, "usefulAt3": 0, "ndcgAt5": 0, "mrr": 0, "legacyHitAt1": 0, "legacyHitAt3": 0, "legacyCoverage": 0})
            continue
        recall_at_5 = len([item for item in items if item["rag"]["relevance"].get("recallAt5")])
        useful_at_3 = len([item for item in items if item["rag"]["relevance"].get("usefulAt3")])
        ndcg = sum(float(item["rag"]["relevance"].get("ndcgAt5") or 0) for item in items) / len(items)
        mrr = sum(float(item["rag"]["relevance"].get("mrr") or 0) for item in items) / len(items)
        legacy_items = [item for item in items if item["rag"].get("legacy", {}).get("expected")]
        legacy_hit_at_1 = len([item for item in legacy_items if item["rag"]["legacy"].get("hitAt1")])
        legacy_hit_at_3 = len([item for item in legacy_items if item["rag"]["legacy"].get("hitAt3")])
        legacy_coverage = len([item for item in legacy_items if item["rag"]["legacy"].get("rank")])
        summary.append(
            {
                "variant": variant,
                "cases": len(items),
                "recallAt5": round(recall_at_5 / len(items), 3),
                "usefulAt3": round(useful_at_3 / len(items), 3),
                "ndcgAt5": round(ndcg, 3),
                "mrr": round(mrr, 3),
                "legacyHitAt1": round(legacy_hit_at_1 / len(legacy_items), 3) if legacy_items else 0,
                "legacyHitAt3": round(legacy_hit_at_3 / len(legacy_items), 3) if legacy_items else 0,
                "legacyCoverage": round(legacy_coverage / len(legacy_items), 3) if legacy_items else 0,
            }
        )
    return summary


def render_eval_report(summary: dict[str, Any]) -> str:
    variant_rows = "\n".join(f"| {item['variant']} | {item['success']} / {item['total']} | {item['successRate']} | {item['avgDurationMs']} ms |" for item in summary["variantSummary"])
    rag_rows = "\n".join(f"| {item['variant']} | {item['cases']} | {item.get('recallAt5', 'n/a')} | {item.get('usefulAt3', 'n/a')} | {item.get('ndcgAt5', 'n/a')} | {item.get('mrr', 'n/a')} | {item.get('legacyHitAt1', 'n/a')} | {item.get('legacyHitAt3', 'n/a')} |" for item in summary.get("ragSummary", []))
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

| Variant | Cases | Recall@5 | Useful@3 | nDCG@5 | MRR | Legacy Hit@1 | Legacy Hit@3 |
|---|---:|---:|---:|---:|---:|---:|---:|
{rag_rows}

Metric definitions:

- Recall@5: top 5 contains at least one relevant evidence item, graded by semantic relevance instead of fixed ids.
- Useful@3: top 3 contains evidence graded as useful for patch generation.
- nDCG@5: ranking quality with graded relevance from 0 to 3.
- MRR: reciprocal rank of the first useful evidence item.
- Legacy Hit@1/Hit@3: fixed `expectedRagIds` metrics kept only as a regression/reference signal.

## Case Runs

| Variant | Case | Category | Status | Duration | RAG | Report |
|---|---|---|---|---:|---|---|
{rows}
"""


def format_rag_cell(rag: dict[str, Any]) -> str:
    relevance = rag.get("relevance") or {}
    if not relevance:
        return "n/a"
    rank = relevance.get("bestRank") or "miss"
    top = relevance.get("topHit") or "none"
    return f"usefulRank={rank}; top={top}; ndcg@5={relevance.get('ndcgAt5')}"
