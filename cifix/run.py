from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agents.failure_triage_agent import run_failure_triage_agent
from .agents.memory_writer_agent import run_memory_writer_agent
from .agents.patch_agent import run_patch_agent
from .agents.repair_memory_agent import run_repair_memory_agent
from .agents.report_writer_agent import run_report_writer_agent
from .agents.reproducer_agent import run_reproducer_agent
from .agents.review_agent import run_review_agent
from .agents.setup_agent import run_setup_agent
from .agents.test_agent import run_test_agent
from .agents.github_writer_agent import run_github_writer_agent
from .github import load_github_context
from .core.trace import step
from .rag import embedding_config_from_flags, vector_db_from_flags
from .tools.workspace import infer_command, infer_setup_command, map_repo, prepare_workspace, read_log, repo_looks_like_github_slug


def run_cifix(flags: dict[str, Any]) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    out_root = Path(flags.get("out") or "artifacts").resolve()
    run_dir = out_root / run_id
    workspace_dir = run_dir / "workspace"
    memory_path = Path(flags.get("memory-path") or "artifacts/memory/verified-repairs.json").resolve()
    vector_db = vector_db_from_flags(flags)
    embedding_config = embedding_config_from_flags(flags)
    trace: list[dict[str, Any]] = []
    run_dir.mkdir(parents=True, exist_ok=True)

    github_context = load_github_context(
        pr_url=flags.get("url") or flags.get("pr-url"),
        owner_repo=flags.get("repo") if repo_looks_like_github_slug(flags.get("repo")) else None,
        pull_number=flags.get("pr"),
        run_id=flags.get("run-id"),
        job_id=flags.get("job"),
        token=os.getenv(flags.get("token-env") or "GITHUB_TOKEN"),
    )
    prepare_workspace(flags, github_context, workspace_dir, trace)
    command = flags.get("command") or infer_command(workspace_dir)
    setup_command = flags.get("setup-command") or infer_setup_command(workspace_dir, enabled=bool(github_context))
    setup_result = run_setup_agent(workspace_dir=workspace_dir, setup_command=setup_command, trace=trace)
    raw_log = read_log(flags.get("log")) if flags.get("log") else (github_context or {}).get("rawLog", "")
    repo_map = map_repo(workspace_dir)

    reproduction = run_reproducer_agent(workspace_dir=workspace_dir, command=command, trace=trace)
    fingerprint = run_failure_triage_agent(raw_log=raw_log, command=command, repo_map=repo_map, github_context=github_context, reproduction=reproduction, trace=trace)
    if flags.get("no-memory"):
        playbook_hits = []
        trace.append(step("RepairMemoryAgent", {"disabled": True}, []))
    else:
        playbook_hits = run_repair_memory_agent(fingerprint=fingerprint, trace=trace, memory_path=memory_path, raw_log=raw_log, reproduction=reproduction, vector_db=vector_db, embedding_config=embedding_config)
    patch_result = run_patch_agent(flags=flags, workspace_dir=workspace_dir, fingerprint=fingerprint, playbook_hits=playbook_hits, raw_log=raw_log, reproduction=reproduction, repo_map=repo_map, trace=trace)
    candidates = patch_result["candidates"][:1] if flags.get("single-candidate") else patch_result["candidates"]
    if flags.get("single-candidate"):
        trace.append(step("BaselineMode", {"mode": "single-candidate"}, {"candidateCount": len(candidates)}))
    test_results = run_test_agent(workspace_dir=workspace_dir, candidates=candidates, command=command, playbook_hits=playbook_hits, run_dir=run_dir, trace=trace)
    tournament = run_review_agent(workspace_dir=workspace_dir, test_results=test_results, trace=trace)
    selected = tournament["selected"]
    memory_write = (
        {"written": False, "reason": "memory disabled"}
        if flags.get("no-memory")
        else run_memory_writer_agent(memory_path=memory_path, fingerprint=fingerprint, selected=selected, command=command, trace=trace)
    )
    github_write = run_github_writer_agent(
        flags=flags,
        workspace_dir=workspace_dir,
        github_context=github_context,
        selected=selected,
        fingerprint=fingerprint,
        command=command,
        run_id=run_id,
        trace=trace,
    )

    run_report_writer_agent(
        run_dir=run_dir,
        run_id=run_id,
        command=command,
        setup_result=setup_result,
        fingerprint=fingerprint,
        playbook_hits=playbook_hits,
        reproduction=reproduction,
        model_diagnosis=patch_result["modelDiagnosis"],
        tournament=tournament,
        selected=selected,
        memory_write=memory_write,
        github_write=github_write,
        github_context=github_context,
        trace=trace,
        started_at=started_at,
    )
    return {
        "runId": run_id,
        "status": "success" if selected and selected["verification"]["passed"] else "needs_attention",
        "paths": {
            "patch": str(run_dir / "patch.diff"),
            "report": str(run_dir / "report.md"),
            "trace": str(run_dir / "trace.json"),
            "prComment": str(run_dir / "pr-comment.md"),
            "githubWrite": str(run_dir / "github-write.json"),
        },
        "githubWrite": github_write,
    }
