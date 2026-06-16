from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.trace import step
from ..tools.patch import apply_candidate, restore_baseline


def run_review_agent(*, workspace_dir: Path, test_results: list[dict[str, Any]], trace: list[dict]) -> dict[str, Any]:
    ranked = sorted(test_results, key=lambda item: item["rankingScore"])
    selected = ranked[0] if ranked else None
    restore_baseline(workspace_dir)
    if selected:
        apply_candidate(workspace_dir, selected)
    tournament = {
        "candidates": ranked,
        "selected": selected,
        "summary": {
            "selected": selected["id"] if selected else None,
            "ranking": [{"id": c["id"], "passed": c["verification"]["passed"], "riskScore": c["riskScore"], "rankingScore": c["rankingScore"], "riskTags": c.get("riskTags", [])} for c in ranked],
        },
    }
    trace.append(step("ReviewAgent", {"candidateCount": len(ranked)}, tournament["summary"]))
    return tournament

