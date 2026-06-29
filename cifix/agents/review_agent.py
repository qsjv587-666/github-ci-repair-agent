from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.trace import step
from ..model import generate_model_review, model_config_from_env
from ..tools.patch import apply_candidate, restore_baseline


def run_review_agent(*, workspace_dir: Path, test_results: list[dict[str, Any]], trace: list[dict], flags: dict[str, Any] | None = None, fingerprint: dict[str, Any] | None = None, playbook_hits: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    ranked = sorted(test_results, key=lambda item: item["rankingScore"])
    model_config = model_config_from_env(flags or {})
    model_result = safe_generate_model_review(
        fingerprint=fingerprint or {},
        playbook_hits=playbook_hits or [],
        test_results=ranked,
        model_config=model_config,
    )
    trace.append(step("LLMReviewAgent", {"enabled": model_config["enabled"], "provider": model_config["provider"], "model": model_config["model"]}, model_result["diagnosis"]))
    selected = select_candidate(ranked, model_result.get("review"))
    restore_baseline(workspace_dir)
    if selected:
        apply_candidate(workspace_dir, selected)
    tournament = {
        "candidates": ranked,
        "selected": selected,
        "summary": {
            "selected": selected["id"] if selected else None,
            "ranking": [{"id": c["id"], "passed": c["verification"]["passed"], "riskScore": c["riskScore"], "rankingScore": c["rankingScore"], "riskTags": c.get("riskTags", [])} for c in ranked],
            "llmRecommended": (model_result.get("review") or {}).get("recommendedCandidateId"),
        },
        "llmReview": model_result,
    }
    trace.append(step("ReviewAgent", {"candidateCount": len(ranked)}, tournament["summary"]))
    return tournament


def safe_generate_model_review(**kwargs: Any) -> dict[str, Any]:
    try:
        return generate_model_review(**kwargs)
    except Exception as error:
        return {"review": None, "diagnosis": {"error": str(error), "fallback": "rule ranking"}}


def select_candidate(ranked: list[dict[str, Any]], review: dict[str, Any] | None) -> dict[str, Any] | None:
    if not ranked:
        return None
    recommended_id = (review or {}).get("recommendedCandidateId")
    if recommended_id:
        recommended = next((candidate for candidate in ranked if candidate.get("id") == recommended_id), None)
        if recommended and passes_deterministic_gate(recommended):
            return recommended
    return ranked[0]


def passes_deterministic_gate(candidate: dict[str, Any]) -> bool:
    if not candidate.get("verification", {}).get("passed"):
        return False
    risk_tags = set(candidate.get("riskTags", []))
    if risk_tags.intersection({"noop", "test-change", "possible-overfit"}):
        return False
    if not candidate.get("edits"):
        return False
    return True
