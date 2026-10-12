"""SERBITO-613: .github/workflows/ci-red.yml opens one Jira issue per red repo + workflow.

The run script is executed here with stub gh and curl: no network, no real Jira.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
RED = yaml.safe_load((WORKFLOWS / "ci-red.yml").read_text("utf-8"))
# PyYAML reads the `on:` key as True (YAML 1.1)
ON = RED[True]


def _triggers(wf):
    on = wf[True]
    return set(on) if isinstance(on, dict) else {on} if isinstance(on, str) else set(on)


def test_watches_every_workflow_that_runs_outside_pull_requests():
    """A new workflow (deploy, cron, cleanup) must be added to the list, or its red run is silent."""
    expected = set()
    for f in WORKFLOWS.glob("*.y*ml"):
        wf = yaml.safe_load(f.read_text("utf-8"))
        if f.name != "ci-red.yml" and _triggers(wf) - {"pull_request"}:
            expected.add(wf["name"])
    assert set(ON["workflow_run"]["workflows"]) == expected
    assert ON["workflow_run"]["types"] == ["completed"]


def test_only_red_runs_on_main_or_a_tag_and_never_a_pull_request():
    cond = " ".join(RED["jobs"]["jira"]["if"].split())
    for part in ("github.event.workflow_run.conclusion == 'failure'",
                 "github.event.workflow_run.event != 'pull_request'",
                 "github.event.workflow_run.head_repository.full_name == github.repository",
                 "github.event.workflow_run.head_branch == 'main'",
                 "startsWith(github.event.workflow_run.head_branch, 'v')"):
        assert part in cond


def test_minimal_permissions_and_no_actions():
    assert RED["permissions"] == {}
    job = RED["jobs"]["jira"]
    assert job["permissions"] == {"actions": "read"}
    assert job["runs-on"] == "ubuntu-24.04" and job["timeout-minutes"] <= 10
    assert all("uses" not in s for s in job["steps"])  # nothing to pin, nothing checked out


STEP = RED["jobs"]["jira"]["steps"][0]
LOG = "test\tpytest\t2026-10-12T10:00:00.1Z E   AssertionError: box\n" \
      "test\tpytest\t2026-10-12T10:00:00.2Z FAILED tests/test_x.py::test_y\n"


@pytest.fixture
def run_step(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (tmp_path / "log.txt").write_text(LOG)
    (bin_ / "gh").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = api ]; then echo "test / pytest"; exit 0; fi\n'
        f'cat "{tmp_path}/log.txt"\n')
    # curl: logs the URL and the request body; a search answers with search.json
    (bin_ / "curl").write_text(
        "#!/bin/sh\n"
        'for a; do url="$a"; done\n'
        f'body="$(tr -d "\\n")"; printf "%s %s\\n" "$url" "$body" >> "{tmp_path}/curl.log"\n'
        f'case "$url" in *search/jql) cat "{tmp_path}/search.json";; *issue) echo \'{{"key":"SERBITO-9"}}\';; esac\n')
    for f in bin_.iterdir():
        f.chmod(0o755)
    env = {"PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}", "JIRA_SITE": "https://j",
           "JIRA_EMAIL": "bot@example.com", "JIRA_API_TOKEN": "t", "GH_TOKEN": "g",
           "REPO": "alxndr-bnd/gtd", "RUN_ID": "42", "RUN_ATTEMPT": "1",
           "RUN_URL": "https://github.com/alxndr-bnd/gtd/actions/runs/42", "WORKFLOW": "CI",
           "HEAD_BRANCH": "main", "HEAD_SHA": "a" * 40, "EVENT": "push"}

    def run(found=(), **over):
        (tmp_path / "search.json").write_text(json.dumps({"issues": [
            {"key": k, "fields": {"summary": s}} for k, s in found]}))
        r = subprocess.run(["bash", "-c", STEP["run"]], cwd=tmp_path, env={**env, **over},
                           capture_output=True, text=True, timeout=30)
        log = (tmp_path / "curl.log").read_text().splitlines() if (tmp_path / "curl.log").exists() else []
        return r, log
    return run


def test_without_secrets_the_job_skips_green(run_step):
    r, calls = run_step(JIRA_API_TOKEN="")
    assert r.returncode == 0, r.stderr
    assert "::notice::JIRA_EMAIL / JIRA_API_TOKEN are not set" in r.stdout and calls == []


def test_creates_a_highest_bug_with_the_error_lines(run_step):
    r, calls = run_step(found=[("SERBITO-1", "CI red: gtd / Deploy to Cloud Run")])
    assert r.returncode == 0, r.stderr
    assert calls[1].startswith("https://j/rest/api/2/issue ")
    fields = json.loads(calls[1].split(" ", 1)[1])["fields"]
    assert fields["summary"] == "CI red: gtd / CI"
    assert (fields["issuetype"], fields["priority"], fields["labels"]) == (
        {"name": "Bug"}, {"name": "Highest"}, ["ci-red", "bug"])
    d = fields["description"]
    assert "test / pytest" in d and "E   AssertionError: box" in d and "2026-10-12T" not in d
    assert "https://github.com/alxndr-bnd/gtd/actions/runs/42" in d
    assert "::notice::Red run opened https://j/browse/SERBITO-9" in r.stdout


def test_an_open_issue_for_the_same_repo_and_workflow_gets_a_comment(run_step):
    r, calls = run_step(found=[("SERBITO-7", "CI red: gtd / CI")])
    assert r.returncode == 0, r.stderr
    assert len(calls) == 2 and calls[1].startswith("https://j/rest/api/2/issue/SERBITO-7/comment ")
    assert json.loads(calls[1].split(" ", 1)[1])["body"].startswith("Red again.")
