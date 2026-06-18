from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def generate_dashboard(flags: dict[str, Any]) -> dict[str, str]:
    artifacts_root = Path(flags.get("artifacts") or "artifacts").resolve()
    out_path = Path(flags.get("out") or artifacts_root / "dashboard" / "index.html").resolve()
    runs = discover_run_artifacts(artifacts_root)
    evals = discover_eval_artifacts(artifacts_root)
    inspections = discover_inspect_artifacts(artifacts_root)
    statuses = discover_status_artifacts(artifacts_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_dashboard(artifacts_root, runs, evals, inspections, statuses))
    return {"dashboardPath": str(out_path), "runs": str(len(runs)), "evals": str(len(evals)), "inspections": str(len(inspections)), "statuses": str(len(statuses))}


def discover_run_artifacts(root: Path) -> list[dict[str, Any]]:
    runs = []
    for report_path in root.rglob("report.md"):
        run_dir = report_path.parent
        if not run_dir.name.startswith("run_"):
            continue
        fingerprint = read_json(run_dir / "failure-fingerprint.json") or {}
        verification = read_json(run_dir / "verification.json") or {}
        model = read_json(run_dir / "model-diagnosis.json") or {}
        memory = read_json(run_dir / "memory-write.json") or {}
        github_write = read_json(run_dir / "github-write.json") or {}
        github_context = read_json(run_dir / "github-context.json") or {}
        rag_hits = read_json(run_dir / "repair-playbook-hits.json")
        selected = verification.get("selected") or {}
        candidates = verification.get("candidates") or []
        top_rag = first_rag_hit(rag_hits)
        runs.append(
            {
                "id": run_dir.name,
                "path": run_dir,
                "status": "success" if selected.get("passed") else "needs_attention",
                "failureType": fingerprint.get("failureType", "unknown"),
                "errorCode": fingerprint.get("errorCode", "unknown"),
                "project": fingerprint.get("project", "unknown"),
                "platform": fingerprint.get("platform", "unknown"),
                "model": model.get("model") if "model" in model else "disabled",
                "memoryWritten": bool(memory.get("written")),
                "githubRun": github_context.get("runId"),
                "sourcePullNumber": github_context.get("pullNumber"),
                "sourcePullUrl": github_context.get("pullHtmlUrl"),
                "sourceRunUrl": github_context.get("runHtmlUrl"),
                "repairStatus": github_write.get("status"),
                "repairPullNumber": github_write.get("pullNumber"),
                "repairPullUrl": github_write.get("pullUrl"),
                "repairBranch": github_write.get("branch"),
                "autoMergeStatus": (github_write.get("autoMerge") or {}).get("status"),
                "sourceCiAfterMerge": ((github_write.get("autoMerge") or {}).get("sourceStatus") or {}).get("ciState"),
                "candidateCount": len(candidates),
                "passedCandidates": len([item for item in candidates if item.get("verification", {}).get("passed")]),
                "bestCandidate": candidates[0].get("id") if candidates else None,
                "bestRisk": candidates[0].get("riskScore") if candidates else None,
                "topRagId": top_rag.get("id") if top_rag else None,
                "topRagSource": top_rag.get("source") if top_rag else None,
                "topRagScore": top_rag.get("hybridScore") if top_rag else top_rag.get("score") if top_rag else None,
            }
        )
    return sorted(runs, key=lambda item: item["id"], reverse=True)


def discover_eval_artifacts(root: Path) -> list[dict[str, Any]]:
    evals = []
    for summary_path in root.rglob("summary.json"):
        eval_dir = summary_path.parent
        if not eval_dir.name.startswith("eval_"):
            continue
        summary = read_json(summary_path) or {}
        evals.append({**summary, "path": eval_dir})
    return sorted(evals, key=lambda item: item.get("evalId", ""), reverse=True)


def discover_inspect_artifacts(root: Path) -> list[dict[str, Any]]:
    inspections = []
    for context_path in root.rglob("github-context.json"):
        inspect_dir = context_path.parent
        if not inspect_dir.name.startswith("inspect_"):
            continue
        context = read_json(context_path) or {}
        inspections.append({**context, "id": inspect_dir.name, "path": inspect_dir})
    return sorted(inspections, key=lambda item: item["id"], reverse=True)


def discover_status_artifacts(root: Path) -> list[dict[str, Any]]:
    statuses = []
    for status_path in root.rglob("status.json"):
        status_dir = status_path.parent
        if not status_dir.name.startswith("status_"):
            continue
        status = read_json(status_path) or {}
        statuses.append({**status, "id": status_dir.name, "path": status_dir})
    return sorted(statuses, key=lambda item: item["id"], reverse=True)


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def render_dashboard(root: Path, runs: list[dict[str, Any]], evals: list[dict[str, Any]], inspections: list[dict[str, Any]], statuses: list[dict[str, Any]]) -> str:
    success_runs = len([run for run in runs if run["status"] == "success"])
    latest_eval = evals[0] if evals else {}
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CIFix Agent Dashboard</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #172026; background: #f7f8fa; }}
    header {{ padding: 28px 36px 18px; background: #ffffff; border-bottom: 1px solid #dde3ea; }}
    main {{ padding: 24px 36px 40px; }}
    h1 {{ margin: 0 0 6px; font-size: 28px; }}
    h2 {{ margin: 28px 0 12px; font-size: 18px; }}
    .muted {{ color: #5d6b78; font-size: 13px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }}
    .metric {{ background: #ffffff; border: 1px solid #dde3ea; border-radius: 8px; padding: 14px; }}
    .metric strong {{ display: block; font-size: 24px; margin-bottom: 4px; }}
    table {{ width: 100%; border-collapse: collapse; background: #ffffff; border: 1px solid #dde3ea; border-radius: 8px; overflow: hidden; }}
    th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #e8edf2; font-size: 13px; vertical-align: top; }}
    th {{ background: #f0f3f6; color: #44515e; font-weight: 600; }}
    tr:last-child td {{ border-bottom: 0; }}
    a {{ color: #1769aa; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .pill {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; background: #edf7ed; color: #236b2e; }}
    .warn {{ background: #fff4e5; color: #8a5200; }}
    .bad {{ background: #fdecec; color: #9b1c1c; }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }}
  </style>
</head>
<body>
  <header>
    <h1>CIFix Agent Dashboard</h1>
    <div class="muted">Generated {escape(datetime.now(timezone.utc).isoformat())} from {escape(str(root))}</div>
  </header>
  <main>
    <section class="grid">
      <div class="metric"><strong>{len(runs)}</strong><span>runs</span></div>
      <div class="metric"><strong>{success_runs}</strong><span>successful runs</span></div>
      <div class="metric"><strong>{len(evals)}</strong><span>eval reports</span></div>
      <div class="metric"><strong>{len(inspections)}</strong><span>GitHub inspections</span></div>
      <div class="metric"><strong>{len(statuses)}</strong><span>GitHub status snapshots</span></div>
      <div class="metric"><strong>{escape(str(latest_eval.get("successRate", "n/a")))}</strong><span>latest eval success rate</span></div>
    </section>
    <section>
      <h2>Recent Runs</h2>
      {render_runs_table(root, runs)}
    </section>
    <section>
      <h2>GitHub PR Status</h2>
      {render_status_table(root, statuses)}
    </section>
    <section>
      <h2>Eval Reports</h2>
      {render_evals_table(root, evals)}
    </section>
    <section>
      <h2>GitHub Inspections</h2>
      {render_inspections_table(root, inspections)}
    </section>
  </main>
</body>
</html>
"""


def render_runs_table(root: Path, runs: list[dict[str, Any]]) -> str:
    if not runs:
        return "<p class=\"muted\">No runs found.</p>"
    rows = "\n".join(
        f"""<tr>
  <td>{link(root, run["path"] / "report.md", run["id"])}</td>
  <td><span class="pill {'warn' if run['status'] != 'success' else ''}">{escape(run["status"])}</span></td>
  <td>{escape(run["platform"])} / {escape(run["project"])}<br>{external_link(run.get("sourcePullUrl"), f"source PR #{run.get('sourcePullNumber')}")}</td>
  <td>{escape(run["failureType"])}<br><span class="muted">{escape(run["errorCode"])}</span></td>
  <td>{escape(str(run["candidateCount"]))} candidates<br>{escape(str(run["passedCandidates"]))} passed<br><span class="muted">{escape(str(run.get("bestCandidate") or "n/a"))} risk={escape(str(run.get("bestRisk") or "n/a"))}</span></td>
  <td>{escape(str(run.get("topRagId") or "none"))}<br><span class="muted">{escape(str(run.get("topRagSource") or ""))} score={escape(str(run.get("topRagScore") or "n/a"))}</span></td>
  <td>{render_repair_cell(run)}</td>
  <td>{link(root, run["path"] / "patch.diff", "patch")} · {link(root, run["path"] / "trace.json", "trace")} · {link(root, run["path"] / "repair-playbook-hits.json", "rag")} · {link(root, run["path"] / "github-write.json", "write")}</td>
</tr>"""
        for run in runs[:50]
    )
    return f"<table><thead><tr><th>Run</th><th>Status</th><th>Project</th><th>Failure</th><th>Patch Tournament</th><th>Top RAG Evidence</th><th>GitHub Write</th><th>Artifacts</th></tr></thead><tbody>{rows}</tbody></table>"


def render_status_table(root: Path, statuses: list[dict[str, Any]]) -> str:
    if not statuses:
        return "<p class=\"muted\">No GitHub status snapshots found.</p>"
    rows = "\n".join(
        f"""<tr>
  <td>{link(root, item["path"] / "report.md", item["id"])}</td>
  <td>{external_link(item.get("pullUrl"), f"#{item.get('pullNumber')} {item.get('pullTitle') or ''}")}</td>
  <td><span class="pill {status_class(item.get('ciState'))}">{escape(str(item.get("ciState") or "unknown"))}</span></td>
  <td>{escape(str(item.get("headRef")))} -> {escape(str(item.get("baseRef")))}</td>
  <td>{render_latest_run(item.get("latestRun"))}</td>
</tr>"""
        for item in statuses[:30]
    )
    return f"<table><thead><tr><th>Snapshot</th><th>Pull Request</th><th>CI</th><th>Branches</th><th>Latest Run</th></tr></thead><tbody>{rows}</tbody></table>"


def render_evals_table(root: Path, evals: list[dict[str, Any]]) -> str:
    if not evals:
        return "<p class=\"muted\">No eval reports found.</p>"
    rows = "\n".join(
        f"""<tr>
  <td>{link(root, item["path"] / "report.md", item.get("evalId", item["path"].name))}</td>
  <td>{escape(str(item.get("total", 0)))}</td>
  <td>{escape(str(item.get("success", 0)))}</td>
  <td>{escape(str(item.get("successRate", "n/a")))}</td>
  <td>{escape(str(item.get("avgDurationMs", "n/a")))} ms</td>
</tr>"""
        for item in evals[:20]
    )
    return f"<table><thead><tr><th>Eval</th><th>Cases</th><th>Success</th><th>Rate</th><th>Avg Duration</th></tr></thead><tbody>{rows}</tbody></table>"


def render_inspections_table(root: Path, inspections: list[dict[str, Any]]) -> str:
    if not inspections:
        return "<p class=\"muted\">No GitHub inspections found.</p>"
    rows = "\n".join(
        f"""<tr>
  <td>{link(root, item["path"] / "report.md", item["id"])}</td>
  <td>{escape(str(item.get("owner", "")))}/{escape(str(item.get("repo", "")))}</td>
  <td>{escape(str(item.get("pullNumber") or "n/a"))}</td>
  <td>{escape(str(item.get("runId") or "n/a"))}</td>
  <td>{escape(str(item.get("jobId") or "n/a"))}</td>
  <td>{escape(str(item.get("rawLogChars", 0)))}</td>
</tr>"""
        for item in inspections[:30]
    )
    return f"<table><thead><tr><th>Inspect</th><th>Repo</th><th>PR</th><th>Run</th><th>Job</th><th>Log Chars</th></tr></thead><tbody>{rows}</tbody></table>"


def link(root: Path, target: Path, label: str) -> str:
    try:
        href = target.relative_to(root).as_posix()
    except ValueError:
        href = target.as_posix()
    return f"<a href=\"../{escape(href)}\">{escape(str(label))}</a>"


def external_link(url: str | None, label: str) -> str:
    if not url:
        return '<span class="muted">n/a</span>'
    return f"<a href=\"{escape(url)}\">{escape(label)}</a>"


def render_repair_cell(run: dict[str, Any]) -> str:
    status = run.get("repairStatus") or "skipped"
    branch = f"<br><span class=\"muted mono\">{escape(str(run.get('repairBranch') or ''))}</span>" if run.get("repairBranch") else ""
    repair_label = f"repair PR #{run.get('repairPullNumber')}"
    auto_merge = ""
    if run.get("autoMergeStatus"):
        auto_merge = f"<br><span class=\"muted\">auto-merge: {escape(str(run.get('autoMergeStatus')))}"
        if run.get("sourceCiAfterMerge"):
            auto_merge += f" / source CI: {escape(str(run.get('sourceCiAfterMerge')))}"
        auto_merge += "</span>"
    return f"<span class=\"pill {status_class(status)}\">{escape(str(status))}</span><br>{external_link(run.get('repairPullUrl'), repair_label)}{branch}{auto_merge}"


def render_latest_run(run: dict[str, Any] | None) -> str:
    if not run:
        return '<span class="muted">n/a</span>'
    return f"{external_link(run.get('htmlUrl'), str(run.get('id') or 'run'))}<br><span class=\"muted\">{escape(str(run.get('status')))} / {escape(str(run.get('conclusion')))}</span>"


def status_class(status: str | None) -> str:
    if status in {"success", "pr_created", "pushed"}:
        return ""
    if status in {"pending", "pushed_no_pr", "skipped"}:
        return "warn"
    return "bad"


def first_rag_hit(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return None


def escape(value: str) -> str:
    return html.escape(value, quote=True)
