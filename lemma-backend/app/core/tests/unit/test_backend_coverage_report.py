from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_reporter():
    script = (
        Path(__file__).resolve().parents[4] / "scripts" / "backend_coverage_report.py"
    )
    spec = importlib.util.spec_from_file_location("backend_coverage_report", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backend_coverage_report_groups_modules_and_escapes_markdown(tmp_path):
    reporter = _load_reporter()
    coverage = {
        "files": {
            "app/modules/agent_surfaces/services/ingress_service.py": {
                "summary": {"num_statements": 10, "missing_lines": 2}
            },
            "app/modules/agent/runtime.py": {
                "summary": {"num_statements": 20, "missing_lines": 10}
            },
            "app/core/web_search/search_client.py": {
                "summary": {"num_statements": 5, "missing_lines": 0}
            },
            "app/standalone|odd.py": {
                "summary": {"num_statements": 4, "missing_lines": 1}
            },
            "app/modules/agent_surfaces/tests/unit/test_ignored.py": {
                "summary": {"num_statements": 99, "missing_lines": 99}
            },
            "app/core/test_ignored.py": {
                "summary": {"num_statements": 99, "missing_lines": 99}
            },
            "app/modules/agent/conftest.py": {
                "summary": {"num_statements": 99, "missing_lines": 99}
            },
        },
        "totals": {
            "covered_lines": 26,
            "num_statements": 39,
            "percent_covered": 66.666,
            "missing_lines": 13,
        },
    }
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuite tests="3" failures="0" errors="0" skipped="1" time="1.2" />'
    )

    summary = reporter.build_summary(
        coverage, phase="e2e-surfaces", junit_paths=[junit]
    )
    markdown = reporter.render_markdown(summary)

    assert summary["modules"] == [
        {
            "name": "agent",
            "statements": 20,
            "missing": 10,
            "covered": 10,
            "coverage": 50.0,
        },
        {
            "name": "agent_surfaces",
            "statements": 10,
            "missing": 2,
            "covered": 8,
            "coverage": 80.0,
        },
        {
            "name": "core",
            "statements": 5,
            "missing": 0,
            "covered": 5,
            "coverage": 100.0,
        },
        {
            "name": "standalone|odd.py",
            "statements": 4,
            "missing": 1,
            "covered": 3,
            "coverage": 75.0,
        },
    ]
    assert summary["tests"]["tests"] == 3
    assert summary["tests"]["skipped"] == 1
    assert "standalone\\|odd.py" in markdown
    assert "66.7%" in markdown
    assert reporter.comment_marker("e2e-surfaces") in markdown


def test_backend_coverage_report_missing_artifact_fallback():
    reporter = _load_reporter()
    summary = reporter.build_summary(None, phase="unit", junit_paths=[])

    markdown = reporter.render_markdown(summary)

    assert summary["missing_coverage"] is True
    assert "Coverage artifact was not found" in markdown


def test_e2e_union_report_describes_authoritative_module_gate():
    reporter = _load_reporter()
    summary = reporter.build_summary(
        {
            "files": {},
            "totals": {
                "covered_lines": 0,
                "num_statements": 0,
                "percent_covered": 100.0,
                "missing_lines": 0,
            },
        },
        phase="e2e-union",
        junit_paths=[],
    )

    markdown = reporter.render_markdown(summary)

    assert "Authoritative E2E-only union across every shard" in markdown
    assert "each require 80%" in markdown


def test_overall_report_is_one_module_wise_unit_e2e_and_combined_table():
    reporter = _load_reporter()

    def coverage(*, missing: int):
        covered = 10 - missing
        return {
            "files": {
                "app/modules/agent/runtime.py": {
                    "summary": {"num_statements": 10, "missing_lines": missing}
                },
                "app/app.py": {
                    "summary": {"num_statements": 2, "missing_lines": 1}
                },
                "app/core/message_bus.py": {
                    "summary": {"num_statements": 2, "missing_lines": 1}
                },
            },
            "totals": {
                "covered_lines": covered,
                "num_statements": 10,
                "percent_covered": covered * 10.0,
                "missing_lines": missing,
            },
        }

    summary = reporter.build_overall_summary(
        coverage(missing=4),
        coverage(missing=2),
        coverage(missing=1),
        unit_junit_paths=[],
        e2e_junit_paths=[],
    )
    markdown = reporter.render_overall_markdown(summary)

    assert reporter.comment_marker("overall") in markdown
    assert "| Module | Unit | E2E only | Combined |" in markdown
    assert "| agent | 60.0% | 80.0% | 90.0% |" in markdown
    assert "| app.py |" not in markdown
    assert "| core |" not in markdown
    assert "Lowest-Covered Files" not in markdown
    assert "Tests:" not in markdown
    assert "Gates:" not in markdown
    assert "Backend Coverage" not in markdown


def test_backend_coverage_report_cli_writes_markdown_and_summary(tmp_path):
    reporter = _load_reporter()
    coverage_json = tmp_path / "coverage.json"
    coverage_json.write_text(
        json.dumps(
            {
                "files": {
                    "app/modules/agent_surfaces/domain/entities.py": {
                        "summary": {"num_statements": 8, "missing_lines": 0}
                    }
                },
                "totals": {
                    "covered_lines": 8,
                    "num_statements": 8,
                    "percent_covered": 100.0,
                    "missing_lines": 0,
                },
            }
        )
    )
    markdown_out = tmp_path / "comment.md"
    summary_out = tmp_path / "summary.json"

    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            str(Path(reporter.__file__)),
            "--coverage-json",
            str(coverage_json),
            "--phase",
            "unit",
            "--markdown-out",
            str(markdown_out),
            "--summary-json-out",
            str(summary_out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "100.0%" in result.stdout
    assert "100.0%" in markdown_out.read_text()
    assert json.loads(summary_out.read_text())["totals"]["covered_lines"] == 8
