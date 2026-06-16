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
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_dashboard(artifacts_root, runs, evals, inspections))
    return {"dashboardPath": str(out_path), "runs": str(len(runs)), "evals": str(len(evals)), "inspections": str(len(inspections))}


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
        github_context = read_json(run_dir / "github-context.json") or {}
        selected = verification.get("selected") or {}
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


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def render_dashboard(root: Path, runs: list[dict[str, Any]], evals: list[dict[str, Any]], inspections: list[dict[str, Any]]) -> str:
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
      <div class="metric"><strong>{escape(str(latest_eval.get("successRate", "n/a")))}</strong><span>latest eval success rate</span></div>
    </section>
    <section>
      <h2>Recent Runs</h2>
      {render_runs_table(root, runs)}
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
  <td>{escape(run["platform"])} / {escape(run["project"])}</td>
  <td>{escape(run["failureType"])}<br><span class="muted">{escape(run["errorCode"])}</span></td>
  <td>{escape(str(run["model"]))}</td>
  <td>{escape("yes" if run["memoryWritten"] else "no")}</td>
  <td>{link(root, run["path"] / "patch.diff", "patch")} · {link(root, run["path"] / "trace.json", "trace")} · {link(root, run["path"] / "pr-comment.md", "comment")}</td>
</tr>"""
        for run in runs[:50]
    )
    return f"<table><thead><tr><th>Run</th><th>Status</th><th>Project</th><th>Failure</th><th>Model</th><th>Memory</th><th>Artifacts</th></tr></thead><tbody>{rows}</tbody></table>"


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


def escape(value: str) -> str:
    return html.escape(value, quote=True)
