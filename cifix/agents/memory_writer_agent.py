from __future__ import annotations

import json
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

    memory_path.parent.mkdir(parents=True, exist_ok=True)
    records = load_records(memory_path)
    patch_summary = summarize_patch(selected)
    existing = find_existing(records, fingerprint, patch_summary)
    now = datetime.now(timezone.utc).isoformat()
    if existing:
        existing["successCount"] = int(existing.get("successCount", 1)) + 1
        existing["lastVerifiedAt"] = now
        existing["examplePatchIds"] = sorted(set([*existing.get("examplePatchIds", []), selected["id"]]))
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
                "patchSummary": patch_summary,
                "verificationCommands": [command],
                "examplePatchIds": [selected["id"]],
                "successCount": 1,
                "failureCount": 0,
                "confidence": 0.7,
            }
        )
        action = "created"

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


def find_existing(records: list[dict[str, Any]], fingerprint: dict[str, Any], patch_summary: dict[str, Any]) -> dict[str, Any] | None:
    signature = fingerprint.get("normalizedSignature")
    changed_files = patch_summary.get("changedFiles", [])
    for record in records:
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
