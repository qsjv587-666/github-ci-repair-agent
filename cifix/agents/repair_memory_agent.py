from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.trace import step
from ..rag import HybridRepairRAG, build_repair_query, rag_index_path_for

PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def run_repair_memory_agent(*, fingerprint: dict[str, Any], trace: list[dict], memory_path: Path | None = None, raw_log: str = "", reproduction: dict[str, Any] | None = None, vector_db: str = "sqlite", embedding_config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    playbooks = json.loads((PACKAGE_ROOT / "data" / "playbooks.json").read_text())
    verified_repairs = load_verified_repairs(memory_path)
    index_path = rag_index_path_for(memory_path)
    rag = HybridRepairRAG(index_path, vector_db=vector_db, embedding_config=embedding_config)
    rag.rebuild(playbooks=playbooks, repairs=verified_repairs)
    query_text = build_repair_query(fingerprint=fingerprint, raw_log=raw_log, reproduction=reproduction)
    result = rag.retrieve(query_text, top_k=5)
    trace.append(
        step(
            "RepairMemoryAgent",
            {
                "fingerprint": fingerprint["normalizedSignature"],
                "staticPlaybooks": len(playbooks),
                "verifiedRepairs": len(verified_repairs),
                "vectorDb": vector_db,
                "rag": result["stats"],
            },
            result["hits"],
        )
    )
    return result["hits"]


def load_verified_repairs(memory_path: Path | None) -> list[dict[str, Any]]:
    if not memory_path or not memory_path.exists():
        return []
    try:
        loaded = json.loads(memory_path.read_text())
    except json.JSONDecodeError:
        return []
    if isinstance(loaded, dict):
        loaded = loaded.get("repairs", [])
    return loaded if isinstance(loaded, list) else []


def retrieve_verified_repairs(fingerprint: dict[str, Any], repairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored = []
    for repair in repairs:
        repair_fingerprint = repair.get("fingerprint", {})
        score = 0.0
        reasons = []
        if repair_fingerprint.get("normalizedSignature") == fingerprint.get("normalizedSignature"):
            score += 0.5
            reasons.append("exactSignature")
        if repair_fingerprint.get("failureType") == fingerprint.get("failureType"):
            score += 0.2
            reasons.append("failureType")
        if repair_fingerprint.get("errorCode") == fingerprint.get("errorCode"):
            score += 0.15
            reasons.append("errorCode")
        if repair_fingerprint.get("language") == fingerprint.get("language"):
            score += 0.1
            reasons.append("language")
        score += min(float(repair.get("successCount", 1)), 5) * 0.01
        if score > 0.25:
            scored.append(
                {
                    "id": repair.get("id", "verified_repair"),
                    "source": "verified-repair",
                    "failureSignature": repair_fingerprint.get("normalizedSignature"),
                    "failureType": repair_fingerprint.get("failureType"),
                    "errorCode": repair_fingerprint.get("errorCode"),
                    "language": repair_fingerprint.get("language"),
                    "strategy": repair.get("strategy", "Reuse previously verified repair pattern."),
                    "verificationCommands": repair.get("verificationCommands", []),
                    "successCount": repair.get("successCount", 1),
                    "failureCount": repair.get("failureCount", 0),
                    "confidence": repair.get("confidence", 0.7),
                    "score": round(score, 3),
                    "reasons": reasons,
                }
            )
    return sorted(scored, key=lambda item: item["score"], reverse=True)[:3]


def retrieve_playbooks(fingerprint: dict[str, Any], playbooks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored = []
    for playbook in playbooks:
        score = 0.0
        reasons = []
        if playbook.get("failureType") == fingerprint.get("failureType"):
            score += 0.35
            reasons.append("failureType")
        if playbook.get("errorCode") == fingerprint.get("errorCode"):
            score += 0.25
            reasons.append("errorCode")
        if playbook.get("language") == fingerprint.get("language"):
            score += 0.15
            reasons.append("language")
        if playbook.get("failureSignature", "").split(":")[-1] in fingerprint.get("normalizedSignature", ""):
            score += 0.15
            reasons.append("signature")
        score += min(float(playbook.get("confidence", 0)), 1) * 0.1
        if score > 0.2:
            item = dict(playbook)
            item["source"] = "static-playbook"
            item["score"] = round(score, 3)
            item["reasons"] = reasons
            scored.append(item)
    return sorted(scored, key=lambda item: item["score"], reverse=True)[:3]
