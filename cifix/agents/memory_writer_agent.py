from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.trace import step


def run_memory_writer_agent(*, memory_path: Path, fingerprint: dict[str, Any], selected: dict[str, Any] | None, command: str, trace: list[dict]) -> dict[str, Any]:
    if not selected or not selected.get("verification", {}).get("passed"):
        result = {"written": False, "reason": "no verified patch selected"}
        trace.append(step("MemoryWriterAgent", {"memoryPath": str(memory_path)}, result))
        return result
    quality_error = memory_quality_error(selected)
    if quality_error:
        result = {"written": False, "reason": quality_error}
        trace.append(step("MemoryWriterAgent", {"memoryPath": str(memory_path)}, result))
        return result

    memory_path.parent.mkdir(parents=True, exist_ok=True)
    records = load_records(memory_path)
    patch_summary = summarize_patch(selected)
    record_key = build_record_key(fingerprint, patch_summary, selected)
    existing = find_existing(records, fingerprint, patch_summary, selected)
    now = datetime.now(timezone.utc).isoformat()
    if existing:
        existing["successCount"] = int(existing.get("successCount", 1)) + 1
        existing["lastVerifiedAt"] = now
        existing["examplePatchIds"] = sorted(set([*existing.get("examplePatchIds", []), selected["id"]]))
        existing["confidence"] = confidence_for(existing)
        existing["quality"] = quality_for(existing)
        record_id = existing["id"]
        action = "updated"
    else:
        record_id = f"repair_{uuid.uuid4().hex[:10]}"
        records.append(
            {
                "id": record_id,
                "createdAt": now,
                "lastVerifiedAt": now,
                "fingerprint": {
                    "normalizedSignature": fingerprint.get("normalizedSignature"),
                    "failureType": fingerprint.get("failureType"),
                    "errorCode": fingerprint.get("errorCode"),
                    "language": fingerprint.get("language"),
                    "packageManager": fingerprint.get("packageManager"),
                },
                "strategy": selected.get("hypothesis", "Verified patch repaired the CI failure."),
                "recordKey": record_key,
                "patchSummary": patch_summary,
                "verificationCommands": [command],
                "examplePatchIds": [selected["id"]],
                "successCount": 1,
                "failureCount": 0,
                "confidence": 0.72,
                "quality": {
                    "reuseCount": 0,
                    "source": selected.get("source"),
                    "riskTags": selected.get("riskTags", []),
                    "lastOutcome": "verified",
                },
            }
        )
        action = "created"

    records = sorted(records, key=lambda item: (item.get("fingerprint", {}).get("language", ""), item.get("recordKey", item.get("id", ""))))
    memory_path.write_text(json.dumps({"repairs": records}, ensure_ascii=False, indent=2) + "\n")
    result = {"written": True, "action": action, "recordId": record_id, "memoryPath": str(memory_path)}
    trace.append(step("MemoryWriterAgent", {"selected": selected["id"]}, result))
    return result


def load_records(memory_path: Path) -> list[dict[str, Any]]:
    if not memory_path.exists():
        return []
    try:
        loaded = json.loads(memory_path.read_text())
    except json.JSONDecodeError:
        return []
    records = loaded.get("repairs", []) if isinstance(loaded, dict) else loaded
    return records if isinstance(records, list) else []


def find_existing(records: list[dict[str, Any]], fingerprint: dict[str, Any], patch_summary: dict[str, Any], selected: dict[str, Any]) -> dict[str, Any] | None:
    signature = fingerprint.get("normalizedSignature")
    changed_files = patch_summary.get("changedFiles", [])
    record_key = build_record_key(fingerprint, patch_summary, selected)
    for record in records:
        if record.get("recordKey") and record.get("recordKey") == record_key:
            return record
        if record.get("fingerprint", {}).get("normalizedSignature") == signature and record.get("patchSummary", {}).get("changedFiles") == changed_files:
            return record
    return None


def summarize_patch(selected: dict[str, Any]) -> dict[str, Any]:
    edits = selected.get("edits", [])
    return {
        "changedFiles": sorted({edit.get("file") for edit in edits if edit.get("file")}),
        "editCount": len(edits),
        "riskTags": selected.get("riskTags", []),
        "source": selected.get("source"),
    }


def memory_quality_error(selected: dict[str, Any]) -> str | None:
    risk_tags = set(selected.get("riskTags", []))
    if "noop" in risk_tags:
        return "skip noop repair memory"
    if "test-change" in risk_tags or "possible-overfit" in risk_tags:
        return "skip high-risk repair memory"
    if not selected.get("edits"):
        return "skip empty repair memory"
    return None


def build_record_key(fingerprint: dict[str, Any], patch_summary: dict[str, Any], selected: dict[str, Any]) -> str:
    signature = fingerprint.get("normalizedSignature") or "unknown"
    files = ",".join(patch_summary.get("changedFiles", []))
    strategy = normalize_strategy(selected.get("hypothesis", ""))
    return f"{signature}|{files}|{strategy}"


def normalize_strategy(value: str) -> str:
    tokens = re.findall(r"[a-z0-9_]+", value.lower())
    stopwords = {"the", "and", "should", "return", "source", "repair", "fix"}
    return " ".join(token for token in tokens if token not in stopwords)[:120]


def confidence_for(record: dict[str, Any]) -> float:
    success_count = int(record.get("successCount", 1))
    failure_count = int(record.get("failureCount", 0))
    return round(min(0.95, 0.65 + success_count * 0.04 - failure_count * 0.08), 3)


def quality_for(record: dict[str, Any]) -> dict[str, Any]:
    quality = dict(record.get("quality") or {})
    quality["lastOutcome"] = "verified"
    quality["riskTags"] = record.get("patchSummary", {}).get("riskTags", [])
    return quality
