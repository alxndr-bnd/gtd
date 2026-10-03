"""SERBITO-401: еженедельная пересборка и передеплой самого нового релизного тега.

Исправления безопасности Debian попадали в прод только с релизом. Теперь расписание (среда 03:00 UTC)
и ручной запуск берут самый новый тег vX.Y.Z и проходят тот же путь: сборка с сегодняшним APT_REFRESH →
Trivy → кандидат → переключение трафика. Тег образа — <sha>-r<YYYYMMDD>. После каждого деплоя smoke
главной страницы; при сбое трафик возвращается на прежнюю ревизию.

Скрипты шагов запускаются как есть (bash -e -o pipefail, как в GitHub) на временном git-репозитории
с поддельными gcloud / curl / sleep."""
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / ".github" / "workflows" / "deploy.yml"
WORKFLOW = yaml.safe_load(DEPLOY.read_text(encoding="utf-8"))
STEPS = WORKFLOW["jobs"]["deploy"]["steps"]
IMAGE_REPO = WORKFLOW["env"]["IMAGE_REPO"]


def _index(predicate):
    found = [i for i, step in enumerate(STEPS) if predicate(step)]
    assert len(found) == 1, found
    return found[0]


CHECKOUT = _index(lambda s: "actions/checkout@" in s.get("uses", ""))
PICK = _index(lambda s: s.get("id") == "release")
PREV = _index(lambda s: s.get("id") == "prev")
DEPLOY_STEP = _index(lambda s: "gcloud run deploy" in s.get("run", ""))
SMOKE = _index(lambda s: "Smoke check" in s.get("name", ""))


def _clean_env(**extra):
    # Без унаследованных GIT_*: внутри git-хука они указывают на НАСТОЯЩИЙ репозиторий
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    env.update(extra)
    return env


def _run(i, cwd, tmp_path, **env):
    out, genv = tmp_path / "out", tmp_path / "env"
    out.write_text("")
    genv.write_text("")
    script = re.sub(r"\$\{\{\s*env\.(\w+)\s*\}\}", r"${\1}", STEPS[i]["run"])
    r = subprocess.run(["bash", "-eo", "pipefail", "-c", script], cwd=cwd, capture_output=True, text=True,
                       timeout=60, env=_clean_env(GITHUB_OUTPUT=str(out), GITHUB_ENV=str(genv),
                                                  GITHUB_STEP_SUMMARY=os.devnull, **env))
    return r, dict(line.split("=", 1) for f in (out, genv) for line in f.read_text().splitlines())


# ── Запуски ──

def test_weekly_on_wednesday_and_by_hand():
    on = WORKFLOW[True]  # голый ключ `on` PyYAML читает как True
    assert on["push"]["tags"] == ["v*.*.*"]
    assert on["schedule"] == [{"cron": "0 3 * * 3"}]  # среда: во вторник 02:00 UTC обслуживание Cloud SQL
    assert "workflow_dispatch" in on


def test_a_refresh_never_overlaps_a_release_deploy():
    assert WORKFLOW["concurrency"] == {"group": "deploy-production", "cancel-in-progress": False}


def test_checkout_fetches_tags_only_for_a_refresh():
    assert CHECKOUT + 1 == PICK
    assert STEPS[CHECKOUT]["with"]["fetch-depth"] == "${{ github.event_name == 'push' && 1 || 0 }}"


def test_later_steps_use_the_picked_tag_not_the_trigger_ref():
    code = "\n".join(line for line in DEPLOY.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("#"))
    assert "IMAGE" not in WORKFLOW["env"], "IMAGE ставит выбор тега"
    assert "github.sha" not in code and "GITHUB_SHA" not in code  # по расписанию это main
    assert code.count("GITHUB_REF_NAME") == 1  # только выбор тега, для пуша тега


# ── Выбор тега ──

@pytest.fixture
def repo(tmp_path):
    work = tmp_path / "repo"
    work.mkdir()
    env = _clean_env()

    def git(*args):
        return subprocess.run(["git", *args], cwd=work, env=env, check=True, capture_output=True,
                              text=True).stdout.strip()

    def commit(text):
        (work / "app.py").write_text(text)
        git("add", "-A")
        git("commit", "-qm", text)
        return git("rev-parse", "HEAD")

    git("init", "-q", "-b", "main")
    assert Path(git("rev-parse", "--absolute-git-dir")).resolve() == (work / ".git").resolve()
    return work, git, commit


def test_a_tag_push_deploys_its_own_commit(repo, tmp_path):
    work, git, commit = repo
    sha = commit("release")
    git("tag", "v0.2.0")
    r, out = _run(PICK, work, tmp_path, GITHUB_EVENT_NAME="push", GITHUB_REF_NAME="v0.2.0", IMAGE_REPO=IMAGE_REPO)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (out["mode"], out["RELEASE_TAG"], out["IMAGE"]) == ("release", "v0.2.0", f"{IMAGE_REPO}:{sha}")


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
def test_a_refresh_rebuilds_the_newest_release_tag(repo, tmp_path, event):
    work, git, commit = repo
    commit("old")
    git("tag", "v0.9.0")
    newest = commit("newest release")
    git("tag", "v0.10.0")  # порядок версий, не строк
    commit("candidate")
    git("tag", "v0.11.0-rc1")  # не релизный тег
    commit("work on main")
    r, out = _run(PICK, work, tmp_path, GITHUB_EVENT_NAME=event, IMAGE_REPO=IMAGE_REPO)
    assert r.returncode == 0, r.stdout + r.stderr
    assert git("rev-parse", "HEAD") == newest, "checkout на тег"
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    assert (out["mode"], out["RELEASE_TAG"], out["IMAGE"]) == ("refresh", "v0.10.0", f"{IMAGE_REPO}:{newest}-r{day}")


def test_a_refresh_without_a_release_tag_fails(repo, tmp_path):
    work, _, commit = repo
    commit("never released")
    r, _ = _run(PICK, work, tmp_path, GITHUB_EVENT_NAME="schedule", IMAGE_REPO=IMAGE_REPO)
    assert r.returncode != 0 and "::error::No vX.Y.Z tag" in r.stdout


# ── Прежняя ревизия, smoke, откат ──

FAKE_GCLOUD = """#!/bin/sh
echo "$*" >> "$FAKE_LOG"
case "$*" in
  "run services describe"*"--format=json"*) [ -n "$FAKE_JSON" ] && echo "$FAKE_JSON" ;;
  "run services describe"*"status.url"*) echo "https://gtd-x.a.run.app" ;;
  "run services update-traffic"*) exit 0 ;;
  *) exit 2 ;;
esac
"""


@pytest.fixture
def fakes(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {"gcloud": FAKE_GCLOUD, "curl": '#!/bin/sh\nprintf "%s" "$FAKE_CODE"\n',
                       "sleep": "#!/bin/sh\nexit 0\n"}.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    jq = shutil.which("jq")
    path = os.pathsep.join([str(bin_dir), "/usr/bin", "/bin", *([os.path.dirname(jq)] if jq else [])])
    log = tmp_path / "gcloud.log"
    log.write_text("")
    return {"PATH": path, "FAKE_LOG": str(log), "SERVICE": "gtd", "REGION": "europe-west1"}, log


def test_serving_revision_is_read_before_the_deploy(tmp_path, fakes):
    assert PREV < DEPLOY_STEP < SMOKE
    env, _ = fakes
    traffic = {"status": {"traffic": [{"revisionName": "gtd-00041", "percent": 100},
                                      {"revisionName": "gtd-00042", "tag": "candidate"}]}}
    r, out = _run(PREV, tmp_path, tmp_path, FAKE_JSON=json.dumps(traffic), **env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert out["revision"] == "gtd-00041"
    r, out = _run(PREV, tmp_path, tmp_path, FAKE_JSON="", **env)  # сервиса ещё нет
    assert r.returncode == 0 and out["revision"] == "", r.stdout + r.stderr


def test_smoke_runs_after_the_traffic_switch_on_every_deploy():
    smoke = STEPS[SMOKE]
    assert "if" not in smoke and not smoke.get("continue-on-error")
    assert smoke["env"]["PREV_REVISION"] == "${{ steps.prev.outputs.revision }}"


@pytest.mark.parametrize("code, prev, ok, rollback", [
    ("200", "gtd-00041", True, False),
    ("500", "gtd-00041", False, True),
    ("000", "", False, False),
])
def test_smoke_rolls_back_to_the_previous_revision(tmp_path, fakes, code, prev, ok, rollback):
    env, log = fakes
    r, _ = _run(SMOKE, tmp_path, tmp_path, FAKE_CODE=code, PREV_REVISION=prev, **env)
    assert (r.returncode == 0) == ok, r.stdout + r.stderr
    assert ("--to-revisions gtd-00041=100" in log.read_text()) == rollback, log.read_text()
    if not ok:
        assert "::error::" in r.stdout
