from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_POE_BASE_URL = "https://api.poe.com/v1"
DEFAULT_POE_MODEL = "Claude-Opus-4.6"


def model_config_from_env(flags: dict[str, Any]) -> dict[str, Any]:
    base_url = (flags.get("poe-base-url") or os.getenv("POE_BASE_URL") or DEFAULT_POE_BASE_URL).rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"
    return {
        "enabled": bool(flags.get("use-model") or os.getenv("CIFIX_USE_MODEL") == "1"),
        "provider": "poe",
        "apiKey": os.getenv("POE_API_KEY"),
        "baseUrl": base_url,
        "model": flags.get("model") or os.getenv("POE_MODEL") or DEFAULT_POE_MODEL,
    }


def generate_model_patch_candidates(*, workspace_dir: Path, fingerprint: dict[str, Any], playbook_hits: list[dict[str, Any]], raw_log: str, reproduction: dict[str, Any], repo_map: dict[str, Any], model_config: dict[str, Any]) -> dict[str, Any]:
    if not model_config["enabled"]:
        return {"candidates": [], "diagnosis": {"skipped": "model disabled"}}
    if not model_config.get("apiKey"):
        return {"candidates": [], "diagnosis": {"skipped": "POE_API_KEY is not set"}}

    file_contexts = collect_relevant_file_contexts(workspace_dir, fingerprint, repo_map)
    prompt = build_patch_prompt(fingerprint, playbook_hits, raw_log, reproduction, file_contexts)
    response_text = call_poe_chat_completion(
        api_key=model_config["apiKey"],
        base_url=model_config["baseUrl"],
        model=model_config["model"],
        messages=[
            {
                "role": "system",
                "content": "You are CIFix Agent's Patch Agent. Return only valid JSON. Propose safe, minimal code patches for CI failures. Prefer fixing source code over weakening tests unless evidence says the test is wrong.",
            },
            {"role": "user", "content": prompt},
        ],
    )
    parsed = parse_json_object(response_text)
    candidates = normalize_model_candidates(parsed)
    return {
        "candidates": candidates,
        "diagnosis": {
            "provider": model_config["provider"],
            "model": model_config["model"],
            "rawResponsePreview": response_text[:2000],
            "parsedSummary": parsed.get("summary") or parsed.get("diagnosis"),
            "candidateCount": len(candidates),
        },
    }


def generate_model_triage(*, workspace_dir: Path | None, fingerprint: dict[str, Any], raw_log: str, reproduction: dict[str, Any], repo_map: dict[str, Any], model_config: dict[str, Any]) -> dict[str, Any]:
    if not model_config["enabled"]:
        return {"triage": None, "diagnosis": {"skipped": "model disabled"}}
    if not model_config.get("apiKey"):
        return {"triage": None, "diagnosis": {"skipped": "POE_API_KEY is not set"}}

    file_contexts = collect_relevant_file_contexts(workspace_dir, fingerprint, repo_map) if workspace_dir else []
    prompt = build_triage_prompt(fingerprint, raw_log, reproduction, repo_map, file_contexts)
    response_text = call_poe_chat_completion(
        api_key=model_config["apiKey"],
        base_url=model_config["baseUrl"],
        model=model_config["model"],
        messages=[
            {
                "role": "system",
                "content": "You are CIFix Agent's Triage Agent. Return only valid JSON. Diagnose CI failures, identify root cause, affected modules, and a safe suspected fix. Do not propose executable commands.",
            },
            {"role": "user", "content": prompt},
        ],
    )
    parsed = parse_json_object(response_text)
    triage = normalize_model_triage(parsed, repo_map)
    return {
        "triage": triage,
        "diagnosis": {
            "provider": model_config["provider"],
            "model": model_config["model"],
            "rawResponsePreview": response_text[:2000],
            "rootCause": triage.get("rootCause"),
            "affectedModuleCount": len(triage.get("affectedModules", [])),
        },
    }


def generate_model_review(*, fingerprint: dict[str, Any], playbook_hits: list[dict[str, Any]], test_results: list[dict[str, Any]], model_config: dict[str, Any]) -> dict[str, Any]:
    if not model_config["enabled"]:
        return {"review": None, "diagnosis": {"skipped": "model disabled"}}
    if not model_config.get("apiKey"):
        return {"review": None, "diagnosis": {"skipped": "POE_API_KEY is not set"}}

    prompt = build_review_prompt(fingerprint, playbook_hits, test_results)
    response_text = call_poe_chat_completion(
        api_key=model_config["apiKey"],
        base_url=model_config["baseUrl"],
        model=model_config["model"],
        messages=[
            {
                "role": "system",
                "content": "You are CIFix Agent's Review Agent. Return only valid JSON. Review verified patch candidates for correctness, overfit risk, and maintenance risk. You recommend; deterministic gates decide.",
            },
            {"role": "user", "content": prompt},
        ],
    )
    parsed = parse_json_object(response_text)
    review = normalize_model_review(parsed, {result.get("id") for result in test_results})
    return {
        "review": review,
        "diagnosis": {
            "provider": model_config["provider"],
            "model": model_config["model"],
            "rawResponsePreview": response_text[:2000],
            "recommendedCandidateId": review.get("recommendedCandidateId"),
            "riskLevel": review.get("riskLevel"),
        },
    }


def call_poe_chat_completion(*, api_key: str, base_url: str, model: str, messages: list[dict[str, str]]) -> str:
    body = json.dumps({"model": model, "messages": messages, "temperature": 0.2}).encode()
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode())
    return payload.get("choices", [{}])[0].get("message", {}).get("content", "")


def collect_relevant_file_contexts(workspace_dir: Path, fingerprint: dict[str, Any], repo_map: dict[str, Any]) -> list[dict[str, str]]:
    targets = []
    targets.extend(fingerprint.get("failedFiles", []))
    targets.extend(fingerprint.get("changedFiles", []))
    targets.extend([file for file in repo_map.get("files", []) if file.startswith("src/")][:5])
    targets.extend([file for file in repo_map.get("files", []) if file.startswith("test/")][:5])
    seen = set()
    contexts = []
    for relative_file in targets:
        if relative_file in seen:
            continue
        seen.add(relative_file)
        file_path = workspace_dir / relative_file.replace("..", "")
        try:
            contexts.append({"file": relative_file, "content": file_path.read_text()[:6000]})
        except OSError:
            pass
    return contexts[:10]


def build_triage_prompt(fingerprint: dict[str, Any], raw_log: str, reproduction: dict[str, Any], repo_map: dict[str, Any], file_contexts: list[dict[str, str]]) -> str:
    files = "\n\n".join(f"--- {ctx['file']} ---\n{ctx['content']}" for ctx in file_contexts)
    return f"""Diagnose this CI failure and identify likely affected modules.

Output JSON only, matching this shape:
{{
  "rootCause": "specific root cause",
  "failureCategory": "contract_mismatch | import_refactor | type_propagation | lint | assertion | unknown",
  "affectedModules": ["relative/source/file.py"],
  "suspectedFix": "brief safe fix strategy",
  "evidence": ["short evidence item"],
  "confidence": 0.0
}}

Rules:
- Preserve the stable rule fingerprint fields; do not invent a different error code.
- Prefer source-code root cause over changing tests.
- affectedModules must be relative files that exist in the repository when possible.
- Keep evidence grounded in the log, reproduction output, changed files, or file context.

Rule fingerprint:
{json.dumps(fingerprint, ensure_ascii=False, indent=2)}

Repository map:
{json.dumps({"languages": repo_map.get("languages"), "files": repo_map.get("files", [])[:200], "scripts": repo_map.get("scripts", {})}, ensure_ascii=False, indent=2)}

Reproduction output:
{json.dumps({"passed": reproduction.get("passed"), "stdout": (reproduction.get("stdout") or "")[:4000], "stderr": (reproduction.get("stderr") or "")[:4000], "message": reproduction.get("message")}, ensure_ascii=False, indent=2)}

CI log preview:
{raw_log[:4000]}

Relevant files:
{files}
"""


def build_patch_prompt(fingerprint: dict[str, Any], playbook_hits: list[dict[str, Any]], raw_log: str, reproduction: dict[str, Any], file_contexts: list[dict[str, str]]) -> str:
    files = "\n\n".join(f"--- {ctx['file']} ---\n{ctx['content']}" for ctx in file_contexts)
    return f"""Generate 2 or 3 candidate patches for this CI failure.

Output JSON only, matching this shape:
{{
  "summary": "brief diagnosis",
  "candidates": [
    {{
      "id": "short_snake_case_id",
      "hypothesis": "why this patch may fix the failure",
      "riskTags": ["source-change"],
      "edits": [{{"file": "relative/path.js", "from": "exact text to replace", "to": "replacement text"}}]
    }}
  ]
}}

Rules:
- Use exact "from" text that appears in the file.
- Keep patches minimal.
- Prefer source fixes over weakening tests.
- Do not invent files.
- If proposing a test change, add riskTags: ["test-change", "possible-overfit"].

Failure fingerprint:
{json.dumps(fingerprint, ensure_ascii=False, indent=2)}

Repair playbook hits:
{json.dumps(playbook_hits, ensure_ascii=False, indent=2)}

Reproduction output:
{json.dumps({"passed": reproduction.get("passed"), "stdout": (reproduction.get("stdout") or "")[:4000], "stderr": (reproduction.get("stderr") or "")[:4000], "message": reproduction.get("message")}, ensure_ascii=False, indent=2)}

CI log preview:
{raw_log[:4000]}

Relevant files:
{files}
"""


def build_review_prompt(fingerprint: dict[str, Any], playbook_hits: list[dict[str, Any]], test_results: list[dict[str, Any]]) -> str:
    candidate_summaries = []
    for result in test_results[:6]:
        candidate_summaries.append(
            {
                "id": result.get("id"),
                "hypothesis": result.get("hypothesis"),
                "source": result.get("source"),
                "riskTags": result.get("riskTags", []),
                "verification": {
                    "passed": result.get("verification", {}).get("passed"),
                    "exitCode": result.get("verification", {}).get("exitCode"),
                    "message": result.get("verification", {}).get("message"),
                },
                "riskScore": result.get("riskScore"),
                "rankingScore": result.get("rankingScore"),
                "diff": (result.get("diff") or "")[:6000],
            }
        )
    return f"""Review candidate patches for this CI repair.

Output JSON only, matching this shape:
{{
  "recommendedCandidateId": "candidate_id",
  "riskLevel": "low | medium | high",
  "rationale": "why this candidate is safest",
  "candidateAssessments": [
    {{
      "candidateId": "candidate_id",
      "riskLevel": "low | medium | high",
      "strengths": ["..."],
      "concerns": ["..."]
    }}
  ],
  "shouldWriteMemory": true,
  "memoryQuality": {{
    "specificity": "low | medium | high",
    "reuseValue": "low | medium | high",
    "overfitRisk": "low | medium | high"
  }}
}}

Rules:
- Only recommend a candidate that passed verification.
- Prefer source fixes over test changes.
- Penalize noop, possible-overfit, and test-changing patches.
- Do not recommend merge or GitHub write-back directly; deterministic gates decide that.

Fingerprint and LLM triage:
{json.dumps(fingerprint, ensure_ascii=False, indent=2)}

RAG evidence:
{json.dumps(playbook_hits[:5], ensure_ascii=False, indent=2)}

Candidate patches:
{json.dumps(candidate_summaries, ensure_ascii=False, indent=2)}
"""


def parse_json_object(text: str) -> dict[str, Any]:
    trimmed = text.strip()
    try:
        return json.loads(trimmed)
    except json.JSONDecodeError:
        pass
    if "```" in trimmed:
        import re

        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", trimmed, re.I)
        if match:
            return json.loads(match.group(1))
    start, end = trimmed.find("{"), trimmed.rfind("}")
    if start >= 0 and end > start:
        return json.loads(trimmed[start : end + 1])
    raise ValueError("Model response did not contain valid JSON")


def normalize_model_candidates(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = parsed.get("candidates") if isinstance(parsed.get("candidates"), list) else []
    normalized = []
    for index, candidate in enumerate(candidates):
        edits = normalize_edits(candidate.get("edits"))
        if not edits:
            continue
        normalized.append(
            {
                "id": sanitize_id(candidate.get("id") or f"model_patch_{index + 1}"),
                "hypothesis": str(candidate.get("hypothesis") or "Model-proposed patch"),
                "playbookId": candidate.get("playbookId"),
                "riskTags": [str(tag) for tag in candidate.get("riskTags", ["model-generated"])],
                "source": "model",
                "edits": edits,
            }
        )
    return normalized[:3]


def normalize_model_triage(parsed: dict[str, Any], repo_map: dict[str, Any]) -> dict[str, Any]:
    known_files = set(repo_map.get("files") or [])
    affected = parsed.get("affectedModules") if isinstance(parsed.get("affectedModules"), list) else []
    normalized_affected = []
    for item in affected:
        value = str(item)
        if not value or ".." in value:
            continue
        if not known_files or value in known_files:
            normalized_affected.append(value)
    return {
        "rootCause": str(parsed.get("rootCause") or "Model triage did not identify a specific root cause."),
        "failureCategory": sanitize_id(str(parsed.get("failureCategory") or "unknown")),
        "affectedModules": list(dict.fromkeys(normalized_affected))[:10],
        "suspectedFix": str(parsed.get("suspectedFix") or ""),
        "evidence": [str(item) for item in parsed.get("evidence", [])[:8]] if isinstance(parsed.get("evidence"), list) else [],
        "confidence": bounded_float(parsed.get("confidence"), default=0.0),
    }


def normalize_model_review(parsed: dict[str, Any], candidate_ids: set[str]) -> dict[str, Any]:
    recommended = str(parsed.get("recommendedCandidateId") or "")
    if recommended not in candidate_ids:
        recommended = ""
    assessments = []
    raw_assessments = parsed.get("candidateAssessments") if isinstance(parsed.get("candidateAssessments"), list) else []
    for assessment in raw_assessments[:8]:
        if not isinstance(assessment, dict):
            continue
        candidate_id = str(assessment.get("candidateId") or "")
        if candidate_id not in candidate_ids:
            continue
        assessments.append(
            {
                "candidateId": candidate_id,
                "riskLevel": normalize_risk_level(assessment.get("riskLevel")),
                "strengths": [str(item) for item in assessment.get("strengths", [])[:5]] if isinstance(assessment.get("strengths"), list) else [],
                "concerns": [str(item) for item in assessment.get("concerns", [])[:5]] if isinstance(assessment.get("concerns"), list) else [],
            }
        )
    memory_quality = parsed.get("memoryQuality") if isinstance(parsed.get("memoryQuality"), dict) else {}
    return {
        "recommendedCandidateId": recommended,
        "riskLevel": normalize_risk_level(parsed.get("riskLevel")),
        "rationale": str(parsed.get("rationale") or ""),
        "candidateAssessments": assessments,
        "shouldWriteMemory": bool(parsed.get("shouldWriteMemory", True)),
        "memoryQuality": {
            "specificity": normalize_quality_level(memory_quality.get("specificity")),
            "reuseValue": normalize_quality_level(memory_quality.get("reuseValue")),
            "overfitRisk": normalize_risk_level(memory_quality.get("overfitRisk")),
        },
    }


def normalize_edits(edits: Any) -> list[dict[str, str]]:
    if not isinstance(edits, list):
        return []
    normalized = []
    for edit in edits:
        item = {"file": str(edit.get("file") or ""), "from": str(edit.get("from") or ""), "to": str(edit.get("to") or "")}
        if item["file"] and item["from"] and item["to"]:
            normalized.append(item)
    return normalized


def bounded_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return round(max(0.0, min(1.0, parsed)), 3)


def normalize_risk_level(value: Any) -> str:
    normalized = str(value or "").lower()
    return normalized if normalized in {"low", "medium", "high"} else "medium"


def normalize_quality_level(value: Any) -> str:
    normalized = str(value or "").lower()
    return normalized if normalized in {"low", "medium", "high"} else "medium"


def sanitize_id(value: str) -> str:
    import re

    result = re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")
    return result or "model_patch"
