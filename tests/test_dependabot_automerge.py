"""Dependabot auto-merge (SERBITO-400): patch/minor merge on green CI, majors wait for a person.

The merge script runs here against a fake `gh`: it must merge only when every required CI
check exists and passed, and never on a red, missing or unfinished CI.
"""

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
AUTOMERGE = yaml.safe_load((WORKFLOWS / "dependabot-automerge.yml").read_text("utf-8"))
CI = yaml.safe_load((WORKFLOWS / "ci.yml").read_text("utf-8"))
DEPENDABOT = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text("utf-8"))
JOB = AUTOMERGE["jobs"]["automerge"]
STEPS = {step.get("id") or step["name"]: step for step in JOB["steps"]}
DECIDE = STEPS["decide"]
MERGE = next(step for step in JOB["steps"] if "gh pr merge" in step.get("run", ""))
REQUIRED = [line for line in MERGE["env"]["REQUIRED_CHECKS"].splitlines() if line.strip()]
SHA = "0123456789abcdef0123456789abcdef01234567"


def _triggers(workflow):
    # PyYAML reads the bare key `on` as True.
    on = workflow.get("on", workflow.get(True))
    return {on} if isinstance(on, str) else set(on)


def test_runs_on_pull_request_for_dependabot_only_with_minimal_permissions():
    assert _triggers(AUTOMERGE) == {"pull_request"}  # never pull_request_target
    assert AUTOMERGE["permissions"] == {}
    assert "github.actor == 'dependabot[bot]'" in JOB["if"]
    assert JOB["permissions"] == {
        "contents": "write",
        "pull-requests": "write",
        "checks": "read",
    }
    assert MERGE["if"] == "steps.decide.outputs.action == 'merge'"
    assert "--squash" in MERGE["run"] and '--match-head-commit "$HEAD_SHA"' in MERGE["run"]


def test_actions_are_pinned_by_sha_and_nothing_is_checked_out():
    uses = [step["uses"] for step in JOB["steps"] if "uses" in step]
    assert uses and all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", u) for u in uses), uses
    assert not any(u.startswith("actions/checkout@") for u in uses)


def test_required_checks_are_the_pr_ci_jobs_and_ci_needs_no_secrets():
    assert "pull_request" in _triggers(CI)
    ci_checks = {job.get("name", job_id) for job_id, job in CI["jobs"].items()}
    assert REQUIRED and set(REQUIRED) <= ci_checks, (REQUIRED, ci_checks)
    assert MERGE["env"]["SELF_CHECK"] == JOB["name"]
    assert JOB["name"] not in REQUIRED
    # Dependabot PRs get no repo secrets: a CI that needs one is red on every bump.
    assert "secrets." not in (WORKFLOWS / "ci.yml").read_text("utf-8")


def test_dependabot_runs_weekly_on_monday_with_a_minor_patch_group_per_ecosystem():
    for update in DEPENDABOT["updates"]:
        eco = update["package-ecosystem"]
        assert update["schedule"]["interval"] == "weekly", eco
        assert update["schedule"]["day"] == "monday", eco
        group_types = [set(g.get("update-types", [])) for g in update["groups"].values()]
        assert {"minor", "patch"} in group_types, eco  # majors stay out: own PR, label


def _decide(update_type, ecosystem):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(UPDATE_TYPE=update_type, ECOSYSTEM=ecosystem, GITHUB_OUTPUT=os.devnull)
    out = subprocess.run(
        ["bash", "-c", DECIDE["run"]], env=env, capture_output=True, text=True, check=True
    )
    return out.stdout.strip().rsplit(" ", 1)[-1]


@pytest.mark.parametrize(
    ("update_type", "ecosystem", "action"),
    [
        ("version-update:semver-patch", "pip", "merge"),
        ("version-update:semver-minor", "npm_and_yarn", "merge"),
        ("version-update:semver-patch", "docker", "merge"),
        ("version-update:semver-minor", "docker", "review"),  # python 3.14 -> 3.15
        ("version-update:semver-major", "github_actions", "review"),
        ("", "docker", "skip"),  # a digest bump has no semver type
    ],
)
def test_decision_by_update_type(update_type, ecosystem, action):
    assert _decide(update_type, ecosystem) == action


@pytest.fixture
def fake_gh(tmp_path):
    """A `gh` on PATH: `gh api` prints check-runs.json, every call goes to calls.log."""
    if not shutil.which("jq"):
        pytest.skip("the merge script needs jq")
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text(
        '#!/usr/bin/env bash\necho "$*" >> "$FAKE_DIR/calls.log"\n'
        'if [ "$1" = api ]; then cat "$FAKE_DIR/check-runs.json"; fi\n'
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)

    def run(check_runs, wait_seconds=30):
        (tmp_path / "check-runs.json").write_text(json.dumps({"check_runs": check_runs}))
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(MERGE["env"])
        env.update(
            PATH=f"{gh.parent}{os.pathsep}{env['PATH']}",
            FAKE_DIR=str(tmp_path),
            GH_TOKEN="fake",
            GITHUB_REPOSITORY="owner/repo",
            PR_URL="https://github.com/owner/repo/pull/7",
            HEAD_SHA=SHA,
            WAIT_SECONDS=str(wait_seconds),
            POLL_SECONDS="0.1",
        )
        out = subprocess.run(
            ["bash", "-c", MERGE["run"]],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        log = tmp_path / "calls.log"
        merges = [c for c in log.read_text().splitlines() if c.startswith("pr merge")]
        return out, merges

    return run


def _run(name, conclusion="success", status="completed"):
    return {"name": name, "status": status, "conclusion": conclusion}


def test_all_green_merges_with_squash_on_the_tested_commit(fake_gh):
    runs = [_run(c) for c in REQUIRED] + [
        _run("dependabot-automerge", None, "in_progress"),  # its own check run is ignored
        _run("optional-job", "skipped"),
    ]
    out, merges = fake_gh(runs)
    assert out.returncode == 0, out.stderr
    assert merges == [
        f"pr merge https://github.com/owner/repo/pull/7 --squash --match-head-commit {SHA}"
    ]


@pytest.mark.parametrize(
    "conclusion", ["failure", "cancelled", "timed_out", "skipped", "neutral"]
)
def test_a_required_check_that_did_not_pass_blocks_the_merge(fake_gh, conclusion):
    runs = [_run(c) for c in REQUIRED[1:]] + [_run(REQUIRED[0], conclusion)]
    out, merges = fake_gh(runs)
    assert out.returncode == 0, out.stderr
    assert merges == []
    assert "Not merging" in out.stdout


@pytest.mark.parametrize(
    "conclusion", ["failure", "cancelled", "timed_out", "action_required", "stale"]
)
def test_any_other_red_check_blocks_the_merge(fake_gh, conclusion):
    out, merges = fake_gh([_run(c) for c in REQUIRED] + [_run("Analyze", conclusion)])
    assert merges == [] and "Not merging" in out.stdout


def test_a_neutral_other_check_does_not_block_the_merge(fake_gh):
    # SERBITO-417: CodeQL default setup ends `neutral` on a Dependabot PR (javi #52).
    out, merges = fake_gh([_run(c) for c in REQUIRED] + [_run("CodeQL", "neutral")])
    assert out.returncode == 0, out.stderr
    assert len(merges) == 1, out.stdout


def test_missing_ci_never_merges(fake_gh):
    out, merges = fake_gh([_run("some-other-check")], wait_seconds=1)
    assert out.returncode == 1 and merges == []
    assert f"missing: {REQUIRED[0]};" in out.stdout


def test_unfinished_ci_never_merges(fake_gh):
    runs = [_run(c) for c in REQUIRED[1:]] + [_run(REQUIRED[0], None, "in_progress")]
    out, merges = fake_gh(runs, wait_seconds=1)
    assert out.returncode == 1 and merges == []
