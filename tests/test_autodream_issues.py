"""Tests for castor.brain.autodream_issues."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from castor.brain.autodream_issues import IssueTemplate, build_issue_template, file_github_issue


class TestBuildIssueTemplate:
    def test_build_issue_template_title_truncated(self):
        long_text = "A" * 120
        template = build_issue_template(long_text, "RRN-000000000001", "2026-04-01")
        assert len(template.title) <= 80

    def test_build_issue_template_includes_rrn(self):
        rrn = "rrn://craigm26/robot/opencastor-rpi5-hailo/bob-001"
        template = build_issue_template("Motor stall detected.", rrn, "2026-04-01")
        assert rrn in template.body

    def test_build_issue_template_has_autodream_label(self):
        template = build_issue_template("Some issue text.", "RRN-000000000001", "2026-04-01")
        assert "autodream" in template.labels
        assert "needs-triage" in template.labels

    def test_build_issue_template_title_uses_first_sentence(self):
        text = "Motor stall detected. This happened three times. Check the wiring."
        template = build_issue_template(text, "RRN-000000000001", "2026-04-01")
        assert template.title == "Motor stall detected"

    def test_build_issue_template_body_contains_date(self):
        template = build_issue_template("Some issue.", "RRN-1", "2026-04-01")
        assert "2026-04-01" in template.body

    def test_build_issue_template_body_has_attribution(self):
        template = build_issue_template("Some issue.", "RRN-1", "2026-04-01")
        assert "autodream_runner.py" in template.body


class TestFileGithubIssue:
    def test_file_github_issue_dry_run_returns_none(self):
        template = IssueTemplate(
            title="Test issue",
            body="Body text",
            labels=["autodream", "needs-triage"],
        )
        with patch("subprocess.run") as mock_run:
            result = file_github_issue(template, "craigm26/OpenCastor", dry_run=True)
        assert result is None
        mock_run.assert_not_called()

    def test_file_github_issue_calls_gh_cli(self):
        template = IssueTemplate(
            title="Test issue",
            body="Body text",
            labels=["autodream", "needs-triage"],
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://github.com/craigm26/OpenCastor/issues/42\n"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            url = file_github_issue(template, "craigm26/OpenCastor", dry_run=False)

        assert url == "https://github.com/craigm26/OpenCastor/issues/42"
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "gh" in cmd
        assert "issue" in cmd
        assert "create" in cmd
        assert "--repo" in cmd
        assert "craigm26/OpenCastor" in cmd

    def test_file_github_issue_returns_none_on_gh_failure(self):
        template = IssueTemplate(
            title="Test issue",
            body="Body text",
            labels=["autodream", "needs-triage"],
        )
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "HTTP 422: label not found"

        with patch("subprocess.run", return_value=mock_result):
            result = file_github_issue(template, "craigm26/OpenCastor", dry_run=False)

        assert result is None

    def test_file_github_issue_returns_none_on_exception(self):
        template = IssueTemplate(
            title="Test issue",
            body="Body",
            labels=["autodream"],
        )
        with patch("subprocess.run", side_effect=FileNotFoundError("gh not found")):
            result = file_github_issue(template, "craigm26/OpenCastor", dry_run=False)

        assert result is None
