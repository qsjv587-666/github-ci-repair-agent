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


def normalize_edits(edits: Any) -> list[dict[str, str]]:
    if not isinstance(edits, list):
        return []
    normalized = []
    for edit in edits:
        item = {"file": str(edit.get("file") or ""), "from": str(edit.get("from") or ""), "to": str(edit.get("to") or "")}
        if item["file"] and item["from"] and item["to"]:
            normalized.append(item)
    return normalized


def sanitize_id(value: str) -> str:
    import re

    result = re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")
    return result or "model_patch"
