from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cifix.dashboard import generate_dashboard
from cifix.eval import run_eval
from cifix.github import load_github_context, parse_github_url
from cifix.inspect import inspect_github
from cifix.rag import DashScopeEmbeddingProvider, HybridRepairRAG, ZhipuEmbeddingProvider, build_repair_query, create_embedding_provider
from cifix.run import run_cifix
from cifix.tools.command import run_command
from cifix.tools.workspace import infer_setup_command


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
            self.assertEqual(result["total"], 4)
            self.assertEqual(result["success"], 4)
            self.assertEqual(result["successRate"], 1)
            report = Path(result["reportPath"]).read_text()
            self.assertIn("counter-increment-broken", report)
            self.assertIn("lint-unused-var-broken", report)
            self.assertIn("react-button-broken", report)
            self.assertIn("todo-filter-broken", report)

    def test_eval_compare_baselines_runs_multiple_variants(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cifix-baseline-") as out:
            result = run_eval({"cases": "fixtures", "out": out, "memory-path": str(Path(out) / "memory.json"), "compare-baselines": True})
            summary = json.loads(Path(result["summaryPath"]).read_text())
            self.assertEqual(summary["caseCount"], 4)
            self.assertEqual(summary["variants"], ["full", "no_memory", "single_candidate"])
            self.assertEqual(summary["total"], 12)
            self.assertEqual(len(summary["variantSummary"]), 3)
            report = Path(result["reportPath"]).read_text()
            self.assertIn("Variant Summary", report)

    def test_command_policy_rejects_shell_control_tokens(self) -> None:
        result = run_command("npm test && rm -rf /tmp/nope", ".")
        self.assertFalse(result["passed"])
        self.assertEqual(result["exitCode"], 126)
        self.assertIn("safety policy", result["message"])

    def test_setup_command_inference_uses_lockfile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cifix-setup-") as tmp:
            root = Path(tmp)
            (root / "package.json").write_text("{}")
            self.assertIsNone(infer_setup_command(root, enabled=True))
            (root / "package-lock.json").write_text("{}")
            self.assertEqual(infer_setup_command(root, enabled=True), "npm ci")

    def test_github_pr_url_resolves_failed_run_job_and_logs(self) -> None:
        def fake_json(path: str, token: str | None):
            self.assertEqual(token, "token")
            if path == "/repos/acme/widget/pulls/7":
                return {
                    "title": "Fix button state",
                    "html_url": "https://github.com/acme/widget/pull/7",
                    "head": {"sha": "abc123", "repo": {"clone_url": "https://github.com/fork/widget.git"}},
                    "base": {"sha": "base123"},
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
        self.assertEqual(context["changedFiles"], ["src/button.js"])
        self.assertEqual(context["rawLog"], "ERR_ASSERTION stack")

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
            self.assertIn("CIFix Agent Dashboard", html)
            self.assertIn("react-button-broken", html)
            self.assertIn("latest eval success rate", html)

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
            self.assertEqual(hit["retrieval"], "hybrid-bm25-vector")

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


if __name__ == "__main__":
    unittest.main()
