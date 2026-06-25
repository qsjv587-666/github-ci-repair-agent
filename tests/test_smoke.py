from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cifix.dashboard import generate_dashboard
from cifix.eval import discover_cases, grade_hit_relevance, relevance_profile_for_case, run_eval
from cifix.agents.failure_triage_agent import create_failure_fingerprint
from cifix.agents.memory_writer_agent import run_memory_writer_agent
from cifix.agents.github_writer_agent import auto_merge_gate_error, build_repair_branch, run_github_writer_agent
from cifix.github import load_github_context, parse_github_url
from cifix.inspect import inspect_github
from cifix.rag import DashScopeEmbeddingProvider, HybridRepairRAG, ZhipuEmbeddingProvider, build_repair_query, create_embedding_provider
from cifix.rag import vector_db_from_flags
from cifix.run import choose_sandbox_for_repo, run_cifix
from cifix.status import inspect_status
from cifix.tools.command import DEFAULT_SETUP_ALLOWED_PREFIXES, run_command, validate_command
from cifix.tools.workspace import infer_command, infer_setup_command
from cifix.watch import build_dedupe_key, run_watch_once


class CifixSmokeTest(unittest.TestCase):
    def test_local_fixture_produces_fingerprint_playbook_hits_and_patch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cifix-smoke-") as out:
            result = run_cifix(
                {
                    "repo": "fixtures/react-button-broken",
                    "command": "npm test",
                    "log": "fixtures/react-button-broken/ci-fail.log",
                    "out": out,
                    "memory-path": str(Path(out) / "memory.json"),
                }
            )
            self.assertEqual(result["status"], "success")
            run_dir = Path(out) / result["runId"]
            fingerprint = json.loads((run_dir / "failure-fingerprint.json").read_text())
            self.assertEqual(fingerprint["failureType"], "test_assertion_failure")
            hits = json.loads((run_dir / "repair-playbook-hits.json").read_text())
            self.assertGreaterEqual(len(hits), 1)
            patch = (run_dir / "patch.diff").read_text()
            self.assertIn("disabled: Boolean(loading)", patch)
            memory = json.loads((run_dir / "memory-write.json").read_text())
            self.assertTrue(memory["written"])

    def test_eval_runner_summarizes_multiple_fixture_cases(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cifix-eval-") as out:
            result = run_eval({"cases": "fixtures", "out": out, "memory-path": str(Path(out) / "memory.json")})
            self.assertEqual(result["total"], 5)
            self.assertEqual(result["success"], 5)
            self.assertEqual(result["successRate"], 1)
            report = Path(result["reportPath"]).read_text()
            self.assertIn("counter-increment-broken", report)
            self.assertIn("lint-unused-var-broken", report)
            self.assertIn("python-unittest-broken", report)
            self.assertIn("react-button-broken", report)
            self.assertIn("todo-filter-broken", report)
            self.assertIn("RAG Evidence Metrics", report)
            self.assertIn("Recall@5", report)

    def test_python_benchmark_discovers_15_cases_with_rag_expectations(self) -> None:
        cases = discover_cases(Path("fixtures-python"))
        self.assertEqual(len(cases), 15)
        self.assertTrue(all(case["command"] == "python3 -m unittest" for case in cases))
        self.assertTrue(all(case.get("expectedRagIds") for case in cases))
        self.assertTrue(all(relevance_profile_for_case(case).get("concepts") for case in cases))

    def test_python_project_benchmark_declares_pytest_ruff_and_mypy_commands(self) -> None:
        cases = discover_cases(Path("benchmarks/python-projects"))
        self.assertEqual(len(cases), 3)
        commands = {case["command"] for case in cases}
        self.assertEqual(commands, {"python3 -m pytest", "python3 -m ruff check src", "python3 -m mypy src"})
        self.assertTrue(all(case.get("setupCommand") == "python3 -m pip install -r requirements.txt" for case in cases))

    def test_rag_relevance_accepts_useful_verified_repair_memory(self) -> None:
        case = {
            "name": "py03_profile_contract",
            "category": "python_contract_mismatch",
            "expectedChangedFiles": ["src/report.py"],
            "expectedRagIds": ["playbook_python_missing_data_guard"],
        }
        hit = {
            "id": "repair_profile_contract",
            "source": "verified-repair",
            "failureType": "runtime_error",
            "errorCode": "KeyError",
            "language": "python",
            "strategy": "report generation should use the profile field exposed by the service contract.",
            "changedFiles": ["src/report.py"],
        }
        self.assertEqual(grade_hit_relevance(case, hit, relevance_profile_for_case(case)), 3)

    def test_eval_compare_baselines_runs_multiple_variants(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cifix-baseline-") as out:
            result = run_eval({"cases": "fixtures", "out": out, "memory-path": str(Path(out) / "memory.json"), "compare-baselines": True})
            summary = json.loads(Path(result["summaryPath"]).read_text())
            self.assertEqual(summary["caseCount"], 5)
            self.assertEqual(summary["variants"], ["full", "no_memory", "single_candidate"])
            self.assertEqual(summary["total"], 15)
            self.assertEqual(len(summary["variantSummary"]), 3)
            report = Path(result["reportPath"]).read_text()
            self.assertIn("Variant Summary", report)

    def test_eval_rag_modes_runs_cold_and_warm_variants(self) -> None:
        captured = []

        def fake_run_cifix(flags: dict):
            captured.append(flags)
            out = Path(flags["out"])
            out.mkdir(parents=True, exist_ok=True)
            report = out / "report.md"
            patch_path = out / "patch.diff"
            trace = out / "trace.json"
            report.write_text("# report\n")
            patch_path.write_text("")
            trace.write_text("{}")
            (out / "repair-playbook-hits.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "playbook_python_assertion_source_contract",
                            "source": "static-playbook",
                            "failureType": "test_assertion_failure",
                            "errorCode": "ASSERTION",
                            "language": "python",
                            "strategy": "For Python assertion failures, prefer fixing the source contract.",
                        }
                    ]
                )
            )
            return {"runId": "run_fake", "status": "success", "paths": {"report": str(report), "patch": str(patch_path), "trace": str(trace)}}

        with tempfile.TemporaryDirectory(prefix="cifix-eval-rag-modes-") as out:
            with patch("cifix.eval.run_cifix", side_effect=fake_run_cifix):
                result = run_eval({"cases": "fixtures-python", "out": out, "rag-eval-modes": True})
            summary = json.loads(Path(result["summaryPath"]).read_text())
        self.assertEqual(summary["variants"], ["rag_cold_start", "rag_warm_start"])
        self.assertEqual(summary["total"], 30)
        self.assertEqual(len(summary["ragSummary"]), 2)
        self.assertIn("recallAt5", summary["ragSummary"][0])

    def test_python_fixture_can_be_repaired(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cifix-python-fixture-") as out:
            result = run_cifix(
                {
                    "repo": "fixtures/python-unittest-broken",
                    "command": "python3 -m unittest",
                    "log": "fixtures/python-unittest-broken/ci-fail.log",
                    "out": out,
                    "memory-path": str(Path(out) / "memory.json"),
                }
            )
            self.assertEqual(result["status"], "success")
            run_dir = Path(out) / result["runId"]
            fingerprint = json.loads((run_dir / "failure-fingerprint.json").read_text())
            self.assertEqual(fingerprint["language"], "python")
            patch = (run_dir / "patch.diff").read_text()
            self.assertIn("return a + b", patch)

    def test_command_policy_rejects_shell_control_tokens(self) -> None:
        result = run_command("npm test && rm -rf /tmp/nope", ".")
        self.assertFalse(result["passed"])
        self.assertEqual(result["exitCode"], 126)
        self.assertIn("safety policy", result["message"])

    def test_run_command_can_execute_inside_docker_sandbox(self) -> None:
        captured = {}

        def fake_subprocess_run(args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory(prefix="cifix-docker-cmd-") as tmp:
            with patch("cifix.tools.command.subprocess.run", side_effect=fake_subprocess_run):
                result = run_command("npm test", tmp, sandbox={"mode": "docker", "image": "node:20", "network": "none"})

        self.assertTrue(result["passed"])
        self.assertEqual(result["sandbox"]["mode"], "docker")
        self.assertEqual(result["sandbox"]["image"], "node:20")
        self.assertEqual(captured["args"][:3], ["docker", "run", "--rm"])
        self.assertIn("--network", captured["args"])
        self.assertIn("node:20", captured["args"])
        self.assertEqual(captured["args"][-2:], ["npm", "test"])
        self.assertIsNone(captured["kwargs"]["cwd"])

    def test_run_command_timeout_outputs_are_json_safe(self) -> None:
        def fake_subprocess_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=1, output=b"partial out", stderr=b"partial err")

        with tempfile.TemporaryDirectory(prefix="cifix-timeout-cmd-") as tmp:
            with patch("cifix.tools.command.subprocess.run", side_effect=fake_subprocess_run):
                result = run_command("npm test", tmp, sandbox={"mode": "docker", "image": "node:20"})

        self.assertEqual(result["exitCode"], 124)
        self.assertEqual(result["stdout"], "partial out")
        self.assertEqual(result["stderr"], "partial err")
        json.dumps(result)

    def test_setup_command_inference_uses_lockfile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cifix-setup-") as tmp:
            root = Path(tmp)
            (root / "package.json").write_text("{}")
            self.assertIsNone(infer_setup_command(root, enabled=True))
            (root / "package-lock.json").write_text("{}")
            self.assertEqual(infer_setup_command(root, enabled=True), "npm ci")

    def test_python_command_inference(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cifix-python-infer-") as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / "test_sample.py").write_text("import unittest\n")
            self.assertEqual(infer_setup_command(root, enabled=True), None)
            self.assertEqual(infer_command(root), "python3 -m unittest")

    def test_python_ci_command_allowlist_includes_pytest_ruff_mypy(self) -> None:
        self.assertIsNone(validate_command("python3 -m pytest"))
        self.assertIsNone(validate_command("python3 -m ruff check src"))
        self.assertIsNone(validate_command("python3 -m mypy src"))
        self.assertIsNone(validate_command("python3 -m pip install -r requirements.txt", DEFAULT_SETUP_ALLOWED_PREFIXES))

    def test_python_command_overrides_mixed_repo_package_manager(self) -> None:
        fingerprint = create_failure_fingerprint(
            raw_log="",
            command="python3 -m unittest test_report.py",
            repo_map={"languages": ["javascript", "python"], "packageManager": "npm"},
            github_context=None,
            reproduction={"stdout": "", "stderr": "KeyError: 'name'"},
        )
        self.assertEqual(fingerprint["language"], "python")
        self.assertEqual(fingerprint["packageManager"], "python")

    def test_python_lint_and_mypy_fingerprints(self) -> None:
        ruff = create_failure_fingerprint(
            raw_log="src/alerts/formatter.py:1:20: F401 `decimal.Decimal` imported but unused",
            command="python3 -m ruff check src",
            repo_map={"languages": ["python"], "packageManager": "pip"},
            github_context=None,
            reproduction={"stdout": "", "stderr": ""},
        )
        self.assertEqual(ruff["failureType"], "lint_error")
        self.assertEqual(ruff["errorCode"], "F401")
        self.assertEqual(ruff["packageManager"], "python")

        mypy = create_failure_fingerprint(
            raw_log='src/accounts/service.py:12: error: Incompatible return value type (got "str | None", expected "str")  [return-value]',
            command="python3 -m mypy src",
            repo_map={"languages": ["python"], "packageManager": "pip"},
            github_context=None,
            reproduction={"stdout": "", "stderr": ""},
        )
        self.assertEqual(mypy["failureType"], "typecheck_error")
        self.assertEqual(mypy["errorCode"], "mypy:return-value")

    def test_github_pr_url_resolves_failed_run_job_and_logs(self) -> None:
        def fake_json(path: str, token: str | None):
            self.assertEqual(token, "token")
            if path == "/repos/acme/widget/pulls/7":
                return {
                    "title": "Fix button state",
                    "html_url": "https://github.com/acme/widget/pull/7",
                    "head": {"sha": "abc123", "ref": "feature/fix", "repo": {"clone_url": "https://github.com/fork/widget.git", "full_name": "fork/widget"}},
                    "base": {"sha": "base123", "ref": "main", "repo": {"full_name": "acme/widget"}},
                }
            if path == "/repos/acme/widget/pulls/7/files":
                return [{"filename": "src/button.js"}]
            if path == "/repos/acme/widget/actions/runs?head_sha=abc123&per_page=20":
                return {
                    "workflow_runs": [
                        {"id": 100, "name": "build", "status": "completed", "conclusion": "success"},
                        {"id": 101, "name": "test", "status": "completed", "conclusion": "failure", "head_sha": "abc123", "html_url": "https://github.com/acme/widget/actions/runs/101"},
                    ]
                }
            if path == "/repos/acme/widget/actions/runs/101/jobs":
                return {
                    "jobs": [
                        {"id": 201, "name": "lint", "conclusion": "success"},
                        {"id": 202, "name": "unit", "conclusion": "failure", "html_url": "https://github.com/acme/widget/actions/runs/101/job/202"},
                    ]
                }
            raise AssertionError(path)

        with patch("cifix.github.github_json", side_effect=fake_json), patch("cifix.github.github_text", return_value="ERR_ASSERTION stack"):
            context = load_github_context(
                pr_url="https://github.com/acme/widget/pull/7",
                owner_repo=None,
                pull_number=None,
                run_id=None,
                job_id=None,
                token="token",
            )

        self.assertEqual(context["owner"], "acme")
        self.assertEqual(context["repo"], "widget")
        self.assertEqual(context["pullNumber"], 7)
        self.assertEqual(context["runId"], 101)
        self.assertEqual(context["jobId"], 202)
        self.assertEqual(context["cloneUrl"], "https://github.com/fork/widget.git")
        self.assertEqual(context["headRef"], "feature/fix")
        self.assertEqual(context["baseRef"], "main")
        self.assertEqual(context["headRepoFullName"], "fork/widget")
        self.assertEqual(context["changedFiles"], ["src/button.js"])
        self.assertEqual(context["rawLog"], "ERR_ASSERTION stack")

    def test_github_writer_pushes_branch_and_returns_compare_url_without_token(self) -> None:
        calls: list[list[str]] = []

        def fake_run_git(args: list[str], cwd: Path, ssh_key: Path | None = None):
            calls.append(args)
            stdout = " src/login-button.js | 2 +-\n" if args == ["diff", "--stat"] else ""
            return subprocess.CompletedProcess(["git", *args], 0, stdout=stdout, stderr="")

        selected = {
            "id": "patch_source_loading_disabled",
            "verification": {"passed": True, "exitCode": 0},
            "riskTags": ["source-change"],
        }
        context = {
            "owner": "acme",
            "repo": "widget",
            "pullNumber": 7,
            "pullHtmlUrl": "https://github.com/acme/widget/pull/7",
            "headRef": "feature/failing-ci",
            "runHtmlUrl": "https://github.com/acme/widget/actions/runs/1",
        }
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}), patch("cifix.agents.github_writer_agent.run_git", side_effect=fake_run_git):
            result = run_github_writer_agent(
                flags={"create-pr": True},
                workspace_dir=Path("/tmp/workspace"),
                github_context=context,
                selected=selected,
                fingerprint={"failureType": "test_assertion_failure", "errorCode": "ERR_ASSERTION"},
                command="npm test",
                run_id="run_20260616000000_abc123ef",
                trace=[],
            )

        self.assertEqual(result["status"], "pushed_no_pr")
        self.assertEqual(result["branch"], build_repair_branch(pull_number=7, run_id="run_20260616000000_abc123ef"))
        self.assertIn("compare/feature%2Ffailing-ci...ci-repair%2Fpr-7-", result["compareUrl"])
        self.assertIn(["push", "-u", "origin", result["branch"], "--force-with-lease"], calls)

    def test_github_writer_auto_merges_low_risk_repair_pr(self) -> None:
        def fake_run_git(args: list[str], cwd: Path, ssh_key: Path | None = None):
            stdout = " src/login-button.js | 2 +-\n" if args == ["diff", "--stat"] else ""
            return subprocess.CompletedProcess(["git", *args], 0, stdout=stdout, stderr="")

        def fake_github(method: str, path: str, token: str | None, body: dict | None = None):
            self.assertEqual(token, "token")
            if method == "POST" and path == "/repos/acme/widget/pulls":
                return {"number": 8, "html_url": "https://github.com/acme/widget/pull/8"}
            if method == "PUT" and path == "/repos/acme/widget/pulls/8/merge":
                return {"merged": True, "sha": "merge123", "message": "Pull Request successfully merged"}
            raise AssertionError(f"{method} {path}")

        selected = {
            "id": "patch_source_loading_disabled",
            "verification": {"passed": True, "exitCode": 0},
            "riskTags": ["source-change"],
            "edits": [{"file": "src/login-button.js", "from": "disabled: false", "to": "disabled: Boolean(loading)"}],
            "diff": "diff --git a/src/login-button.js b/src/login-button.js\n-    disabled: false\n+    disabled: Boolean(loading)\n",
        }
        context = {
            "owner": "acme",
            "repo": "widget",
            "pullNumber": 7,
            "pullHtmlUrl": "https://github.com/acme/widget/pull/7",
            "headRef": "feature/failing-ci",
            "baseRef": "main",
            "headSha": "oldsha",
            "runHtmlUrl": "https://github.com/acme/widget/actions/runs/1",
        }
        repair_status = {"ciState": "missing", "mergeable": True, "pullUrl": "https://github.com/acme/widget/pull/8"}
        source_status = {"ciState": "success", "pullUrl": "https://github.com/acme/widget/pull/7", "headSha": "newsha"}
        with (
            patch.dict(os.environ, {"GITHUB_TOKEN": "token"}),
            patch("cifix.agents.github_writer_agent.run_git", side_effect=fake_run_git),
            patch("cifix.agents.github_writer_agent.github_request_json", side_effect=fake_github),
            patch("cifix.agents.github_writer_agent.wait_for_pull_ci_success", side_effect=[repair_status, source_status]),
        ):
            result = run_github_writer_agent(
                flags={"create-pr": True, "auto-merge-repair-pr": True},
                workspace_dir=Path("/tmp/workspace"),
                github_context=context,
                selected=selected,
                fingerprint={"failureType": "test_assertion_failure", "errorCode": "ERR_ASSERTION"},
                command="npm test",
                run_id="run_20260616000000_abc123ef",
                trace=[],
            )

        self.assertEqual(result["status"], "pr_created")
        self.assertEqual(result["autoMerge"]["status"], "merged")
        self.assertIn("missing repair PR checks", result["autoMerge"]["repairCiFallback"])
        self.assertEqual(result["autoMerge"]["sourceStatus"]["ciState"], "success")

    def test_auto_merge_gate_blocks_test_changes(self) -> None:
        reason = auto_merge_gate_error(
            flags={},
            repair_base_ref="feature/failing-ci",
            source_head_ref="feature/failing-ci",
            source_base_ref="main",
            selected={
                "riskTags": ["test-change"],
                "edits": [{"file": "test/login-button.test.js"}],
                "diff": "-assert.equal(a, b)\n+assert.ok(a)\n",
            },
        )
        self.assertIn("blocked risk tags", reason or "")

    def test_parse_github_actions_job_url(self) -> None:
        parsed = parse_github_url("https://github.com/acme/widget/actions/runs/101/job/202")
        self.assertEqual(parsed, {"owner": "acme", "repo": "widget", "runId": 101, "jobId": 202})

    def test_inspect_writes_readonly_github_artifacts_without_raw_log_in_context(self) -> None:
        fake_context = {
            "owner": "acme",
            "repo": "widget",
            "pullNumber": 7,
            "pullTitle": "Fix button state",
            "headSha": "abc123",
            "baseSha": "base123",
            "changedFiles": ["src/button.js"],
            "runId": 101,
            "jobId": 202,
            "jobName": "unit",
            "rawLog": "ERR_ASSERTION stack",
            "warnings": [],
        }
        with tempfile.TemporaryDirectory(prefix="cifix-inspect-") as out:
            with patch("cifix.inspect.load_github_context", return_value=fake_context):
                result = inspect_github({"url": "https://github.com/acme/widget/pull/7", "out": out})
            context = json.loads(Path(result["paths"]["context"]).read_text())
            self.assertEqual(context["rawLogChars"], len("ERR_ASSERTION stack"))
            self.assertNotIn("rawLog", context)
            self.assertEqual(Path(result["paths"]["log"]).read_text(), "ERR_ASSERTION stack")

    def test_dashboard_indexes_run_and_eval_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cifix-dashboard-") as out:
            result = run_cifix(
                {
                    "repo": "fixtures/react-button-broken",
                    "command": "npm test",
                    "log": "fixtures/react-button-broken/ci-fail.log",
                    "out": out,
                    "memory-path": str(Path(out) / "memory.json"),
                }
            )
            self.assertEqual(result["status"], "success")
            run_eval({"cases": "fixtures", "out": str(Path(out) / "eval"), "memory-path": str(Path(out) / "memory.json")})
            dashboard = generate_dashboard({"artifacts": out})
            html = Path(dashboard["dashboardPath"]).read_text()
            self.assertIn("CIFix Agent 看板", html)
            self.assertIn("react-button-broken", html)
            self.assertIn("最新 eval 成功率", html)
            self.assertIn("RAG Recall@5", html)
            self.assertIn("Top RAG Evidence", html)

    def test_status_writes_pr_ci_snapshot_artifacts(self) -> None:
        fake_status = {
            "owner": "acme",
            "repo": "widget",
            "pullNumber": 7,
            "pullTitle": "Fix button state",
            "pullUrl": "https://github.com/acme/widget/pull/7",
            "state": "open",
            "merged": False,
            "mergeable": True,
            "headRef": "feature/fix",
            "baseRef": "main",
            "headSha": "abc123",
            "ciState": "success",
            "latestRun": {"id": 101, "name": "CI", "status": "completed", "conclusion": "success", "htmlUrl": "https://github.com/acme/widget/actions/runs/101"},
            "runs": [{"id": 101, "name": "CI", "status": "completed", "conclusion": "success", "htmlUrl": "https://github.com/acme/widget/actions/runs/101"}],
            "checks": {"state": "success", "total": 1, "success": 1, "failure": 0, "pending": 0},
        }
        with tempfile.TemporaryDirectory(prefix="cifix-status-") as out:
            with patch("cifix.status.load_pull_status", return_value=fake_status):
                result = inspect_status({"url": "https://github.com/acme/widget/pull/7", "out": out})
            status = json.loads(Path(result["paths"]["status"]).read_text())
            self.assertEqual(status["ciState"], "success")
            report = Path(result["paths"]["report"]).read_text()
            self.assertIn("CI state: success", report)
            dashboard = generate_dashboard({"artifacts": out})
            html = Path(dashboard["dashboardPath"]).read_text()
            self.assertIn("GitHub PR 状态", html)
            self.assertIn("#7 Fix button state", html)

    def test_hybrid_rag_returns_bm25_and_vector_scores(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cifix-rag-") as out:
            rag = HybridRepairRAG(Path(out) / "repair-rag.sqlite")
            rag.rebuild(
                playbooks=[],
                repairs=[
                    {
                        "id": "repair_button_disabled",
                        "fingerprint": {
                            "normalizedSignature": "javascript:test_assertion_failure:ERR_ASSERTION:ui_state",
                            "failureType": "test_assertion_failure",
                            "errorCode": "ERR_ASSERTION",
                            "language": "javascript",
                            "packageManager": "npm",
                        },
                        "strategy": "Fix disabled state derivation from loading.",
                        "patchSummary": {"changedFiles": ["src/login-button.js"], "riskTags": ["source-change"]},
                        "verificationCommands": ["npm test"],
                        "successCount": 2,
                        "failureCount": 0,
                        "confidence": 0.8,
                    }
                ],
            )
            query = build_repair_query(
                fingerprint={
                    "normalizedSignature": "javascript:test_assertion_failure:ERR_ASSERTION:ui_state",
                    "failureType": "test_assertion_failure",
                    "errorCode": "ERR_ASSERTION",
                    "language": "javascript",
                    "packageManager": "npm",
                    "failedFiles": ["src/login-button.js"],
                    "changedFiles": ["src/login-button.js"],
                    "command": "npm test",
                },
                raw_log="Expected disabled true but got false ERR_ASSERTION",
                reproduction={"stdout": "false !== true", "stderr": ""},
            )
            result = rag.retrieve(query)
            self.assertGreaterEqual(len(result["hits"]), 1)
            hit = result["hits"][0]
            self.assertEqual(hit["id"], "repair_button_disabled")
            self.assertIn("bm25Score", hit)
            self.assertIn("vectorScore", hit)
            self.assertIn("hybridScore", hit)
            self.assertIn("rerankScore", hit)
            self.assertEqual(hit["retrieval"], "hybrid-bm25-vector")

    def test_memory_writer_skips_high_risk_and_records_quality(self) -> None:
        fingerprint = {
            "normalizedSignature": "python:test_assertion_failure:ASSERTION:general",
            "failureType": "test_assertion_failure",
            "errorCode": "ASSERTION",
            "language": "python",
            "packageManager": "python",
        }
        with tempfile.TemporaryDirectory(prefix="cifix-memory-governance-") as tmp:
            memory_path = Path(tmp) / "memory.json"
            skipped = run_memory_writer_agent(
                memory_path=memory_path,
                fingerprint=fingerprint,
                selected={"id": "bad", "verification": {"passed": True}, "riskTags": ["test-change"], "edits": [{"file": "tests/test_x.py"}]},
                command="python3 -m pytest",
                trace=[],
            )
            self.assertFalse(skipped["written"])
            written = run_memory_writer_agent(
                memory_path=memory_path,
                fingerprint=fingerprint,
                selected={"id": "good", "hypothesis": "fix source contract", "source": "rule", "verification": {"passed": True}, "riskTags": ["source-change"], "edits": [{"file": "src/service.py"}]},
                command="python3 -m pytest",
                trace=[],
            )
            self.assertTrue(written["written"])
            records = json.loads(memory_path.read_text())["repairs"]
            self.assertEqual(len(records), 1)
            self.assertIn("recordKey", records[0])
            self.assertIn("quality", records[0])

    def test_chroma_backend_reports_missing_dependency_cleanly(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cifix-chroma-missing-") as out:
            rag = HybridRepairRAG(Path(out) / "repair-rag.sqlite", vector_db="chroma")
            try:
                import chromadb  # noqa: F401
            except ImportError:
                with self.assertRaisesRegex(RuntimeError, "ChromaDB is not installed"):
                    rag.rebuild(playbooks=[], repairs=[])
            else:
                rag.rebuild(playbooks=[], repairs=[])

    def test_embedding_provider_config_supports_dashscope_and_zhipu(self) -> None:
        dashscope = create_embedding_provider({"provider": "dashscope", "model": "text-embedding-v4", "dimensions": 1024, "api_key": "test"})
        zhipu = create_embedding_provider({"provider": "zhipu", "model": "embedding-3", "dimensions": 1024, "api_key": "test"})
        self.assertIsInstance(dashscope, DashScopeEmbeddingProvider)
        self.assertIsInstance(zhipu, ZhipuEmbeddingProvider)
        self.assertEqual(dashscope.endpoint(), "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings")
        self.assertEqual(zhipu.endpoint(), "https://open.bigmodel.cn/api/paas/v4/embeddings")

    def test_vector_db_defaults_to_env(self) -> None:
        with patch.dict(os.environ, {"CIFIX_VECTOR_DB": "chroma"}):
            self.assertEqual(vector_db_from_flags({}), "chroma")
            self.assertEqual(vector_db_from_flags({"vector-db": "sqlite"}), "sqlite")

    def test_docker_sandbox_auto_selects_python_image(self) -> None:
        sandbox = choose_sandbox_for_repo(
            {},
            {"mode": "docker", "image": "node:20", "network": "bridge"},
            {"languages": ["python"]},
        )
        self.assertEqual(sandbox["image"], "python:3.12")

    def test_eval_passes_rag_flags_to_runs(self) -> None:
        captured = []

        def fake_run_cifix(flags: dict):
            captured.append(flags)
            out = Path(flags["out"])
            out.mkdir(parents=True, exist_ok=True)
            report = out / "report.md"
            patch_path = out / "patch.diff"
            trace = out / "trace.json"
            report.write_text("# report\n")
            patch_path.write_text("")
            trace.write_text("{}")
            return {"runId": "run_fake", "status": "success", "paths": {"report": str(report), "patch": str(patch_path), "trace": str(trace)}}

        with tempfile.TemporaryDirectory(prefix="cifix-eval-rag-") as out:
            with patch("cifix.eval.run_cifix", side_effect=fake_run_cifix):
                run_eval(
                    {
                        "cases": "fixtures",
                        "out": out,
                        "vector-db": "chroma",
                        "embedding-provider": "dashscope",
                        "embedding-model": "text-embedding-v4",
                        "embedding-dimensions": "1024",
                    }
                )
        self.assertTrue(captured)
        self.assertEqual(captured[0]["vector-db"], "chroma")
        self.assertEqual(captured[0]["embedding-provider"], "dashscope")
        self.assertEqual(captured[0]["embedding-model"], "text-embedding-v4")

    def test_eval_passes_sandbox_flags_to_runs(self) -> None:
        captured = []

        def fake_run_cifix(flags: dict):
            captured.append(flags)
            out = Path(flags["out"])
            out.mkdir(parents=True, exist_ok=True)
            report = out / "report.md"
            patch_path = out / "patch.diff"
            trace = out / "trace.json"
            report.write_text("# report\n")
            patch_path.write_text("")
            trace.write_text("{}")
            return {"runId": "run_fake", "status": "success", "paths": {"report": str(report), "patch": str(patch_path), "trace": str(trace)}}

        with tempfile.TemporaryDirectory(prefix="cifix-eval-sandbox-") as out:
            with patch("cifix.eval.run_cifix", side_effect=fake_run_cifix):
                run_eval({"cases": "fixtures", "out": out, "sandbox": "docker", "docker-image": "node:20", "docker-network": "none"})

        self.assertTrue(captured)
        self.assertEqual(captured[0]["sandbox"], "docker")
        self.assertEqual(captured[0]["docker-image"], "node:20")
        self.assertEqual(captured[0]["docker-network"], "none")

    def test_watch_once_triggers_failed_pr_and_records_state(self) -> None:
        fake_statuses = [
            {
                "owner": "acme",
                "repo": "widget",
                "pullNumber": 7,
                "pullTitle": "Fix button",
                "pullUrl": "https://github.com/acme/widget/pull/7",
                "state": "open",
                "headSha": "abc123",
                "ciState": "failure",
                "latestRun": {"id": 101, "htmlUrl": "https://github.com/acme/widget/actions/runs/101"},
            },
            {
                "owner": "acme",
                "repo": "widget",
                "pullNumber": 8,
                "pullTitle": "Green PR",
                "pullUrl": "https://github.com/acme/widget/pull/8",
                "state": "open",
                "headSha": "def456",
                "ciState": "success",
                "latestRun": {"id": 102},
            },
        ]
        captured_flags = []

        def fake_run_cifix(flags: dict):
            captured_flags.append(flags)
            return {
                "runId": "run_fake",
                "status": "success",
                "paths": {"prComment": "/tmp/missing-comment.md"},
                "githubWrite": {"enabled": False},
            }

        with tempfile.TemporaryDirectory(prefix="cifix-watch-") as out:
            with (
                patch.dict(os.environ, {"GITHUB_TOKEN": "token"}),
                patch("cifix.watch.list_open_pull_statuses", return_value=fake_statuses),
                patch("cifix.watch.run_cifix", side_effect=fake_run_cifix),
            ):
                result = run_watch_once({"repo": "acme/widget", "out": out, "once": True, "create-pr": True})
                second = run_watch_once({"repo": "acme/widget", "out": out, "once": True, "create-pr": True})

            self.assertEqual(result["summary"]["failedPulls"], 1)
            self.assertEqual(result["summary"]["repairStarted"], 1)
            self.assertEqual(second["summary"]["repairStarted"], 0)
            self.assertEqual(second["summary"]["skipped"], 1)
            self.assertEqual(len(captured_flags), 1)
            self.assertEqual(captured_flags[0]["url"], "https://github.com/acme/widget/pull/7")
            self.assertTrue(captured_flags[0]["create-pr"])
            state = json.loads(Path(result["paths"]["state"]).read_text())
            key = build_dedupe_key("acme/widget", fake_statuses[0])
            self.assertIn(key, state["processed"])

    def test_watch_dry_run_does_not_trigger_or_record(self) -> None:
        fake_statuses = [
            {
                "owner": "acme",
                "repo": "widget",
                "pullNumber": 7,
                "pullTitle": "Fix button",
                "pullUrl": "https://github.com/acme/widget/pull/7",
                "state": "open",
                "headSha": "abc123",
                "ciState": "failure",
                "latestRun": {"id": 101},
            }
        ]
        with tempfile.TemporaryDirectory(prefix="cifix-watch-dry-") as out:
            with (
                patch.dict(os.environ, {"GITHUB_TOKEN": "token"}),
                patch("cifix.watch.list_open_pull_statuses", return_value=fake_statuses),
                patch("cifix.watch.run_cifix") as run_mock,
            ):
                result = run_watch_once({"repo": "acme/widget", "out": out, "once": True, "dry-run": True})

            self.assertEqual(result["summary"]["dryRun"], 1)
            run_mock.assert_not_called()
            state = json.loads(Path(result["paths"]["state"]).read_text())
            self.assertEqual(state["processed"], {})

    def test_watch_can_comment_source_pr_after_repair(self) -> None:
        fake_status = {
            "owner": "acme",
            "repo": "widget",
            "pullNumber": 7,
            "pullTitle": "Fix button",
            "pullUrl": "https://github.com/acme/widget/pull/7",
            "state": "open",
            "headSha": "abc123",
            "ciState": "failure",
            "latestRun": {"id": 101},
        }

        with tempfile.TemporaryDirectory(prefix="cifix-watch-comment-") as out:
            comment_path = Path(out) / "pr-comment.md"
            comment_path.write_text("CI repair summary")

            def fake_run_cifix(flags: dict):
                return {
                    "runId": "run_fake",
                    "status": "success",
                    "paths": {"prComment": str(comment_path)},
                    "githubWrite": {"pullUrl": "https://github.com/acme/widget/pull/9"},
                }

            with (
                patch.dict(os.environ, {"GITHUB_TOKEN": "token"}),
                patch("cifix.watch.list_open_pull_statuses", return_value=[fake_status]),
                patch("cifix.watch.run_cifix", side_effect=fake_run_cifix),
                patch("cifix.watch.create_pr_comment", return_value={"id": 55, "html_url": "https://github.com/acme/widget/pull/7#issuecomment-55"}) as comment_mock,
            ):
                result = run_watch_once({"repo": "acme/widget", "out": out, "once": True, "comment-source-pr": True})

            self.assertEqual(result["summary"]["repairStarted"], 1)
            comment_mock.assert_called_once()
            event = result["summary"]["events"][0]
            self.assertEqual(event["repair"]["sourceComment"]["status"], "commented")


if __name__ == "__main__":
    unittest.main()
