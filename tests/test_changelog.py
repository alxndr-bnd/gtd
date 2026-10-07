"""CHANGELOG.md и релиз (SERBITO-329): формат разбирается, у каждого пункта есть английский и русский текст,
версии идут сверху вниз, у каждого тега vX.Y.Z есть запись с его датой, а релизный скрипт без записи не выпускает.
Ничего не пропускается: тесты гоняются в git-checkout репозитория, и теги там есть."""
import os
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

import changelog as C

ROOT = Path(__file__).resolve().parent.parent
TEXT = C.PATH.read_text(encoding="utf-8")
CYR = re.compile("[а-яё]", re.I)


def git(*args, cwd=ROOT, env=None):
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout


def test_changelog_parses_with_links_in_sync():
    releases = C.parse(TEXT)
    assert releases[0].version == C.UNRELEASED and len(C.released(releases)) >= 16
    assert C.with_links(TEXT, releases) == TEXT  # ссылки сравнения внизу — ровно те, что пересобирает release


def test_every_entry_in_english_and_russian():
    for r in C.parse(TEXT):
        for en, ru in r.entries:
            assert en.strip() and not CYR.search(en), (r.version, en)
            assert CYR.search(ru), (r.version, ru)
            assert not re.search(r"SERBITO-\d+|GTD-\d+", en + ru), (r.version, en)  # для людей, не номера тикетов


def test_every_tag_has_an_entry_with_its_date():
    tags = git("tag", "--list", "v*.*.*").split()
    assert "v0.1.0" in tags and "v0.16.0" in tags, "нет тегов: git fetch --tags"
    done = {r.version: r.date for r in C.released(C.parse(TEXT))}
    for tag in tags:
        assert tag[1:] in done, f"{tag}: нет записи в CHANGELOG.md"
        assert done[tag[1:]] == git("log", "-1", "--format=%cs", tag).strip(), f"{tag}: дата не та"
    # Без тега может быть только самая новая запись: релизный скрипт гоняет тесты до того, как поставит тег
    untagged = [v for v in done if f"v{v}" not in tags]
    newest_tag = max((C.vkey(t[1:]) for t in tags))
    assert len(untagged) <= 1 and all(C.vkey(v) > newest_tag for v in untagged), untagged


GOOD = """# Changelog

Free text.

## [Unreleased]

## [0.2.0] - 2026-09-26

### Added
- Two
  - RU: Два

## [0.1.0] - 2026-09-25

### Fixed
- One
  - RU: Один
"""


@pytest.mark.parametrize("bad, why", [
    (GOOD.replace("  - RU: Два\n", ""), "RU"),                                       # пункт без перевода
    (GOOD.replace("- Two\n  - RU: Два", "  - RU: Два\n- Two"), "RU"),                 # перевод до пункта
    (GOOD.replace("### Added", "### Improved"), "unknown section"),
    (GOOD.replace("## [0.2.0] - 2026-09-26", "## [0.2.0]"), "date"),
    (GOOD.replace("2026-09-26", "2026-02-30"), "bad date"),
    (GOOD.replace("0.2.0", "0.0.9"), "newer"),                                       # порядок версий
    (GOOD.replace("## [Unreleased]\n", "") + "\n## [Unreleased]\n", "Unreleased"),   # Unreleased не первым
    (GOOD.replace("- One", "* One"), "unexpected"),
    (GOOD.replace("### Fixed\n- One\n  - RU: Один\n", ""), "no entries"),
    (GOOD + "\n[0.1.0]: https://example.com\n\nmore text\n", "link"),
])
def test_parser_rejects_malformed(bad, why):
    with pytest.raises(C.ChangelogError, match=why):
        C.parse(bad)


def test_release_turns_unreleased_into_the_version():
    src = GOOD.replace("## [Unreleased]\n", "## [Unreleased]\n\n### Changed\n- Three\n  - RU: Три\n")
    out = C.release(src, "0.3.0", "2026-09-28")
    top = C.parse(out)[:2]
    assert (top[0].version, top[0].entries) == (C.UNRELEASED, [])
    assert (top[1].version, top[1].date, top[1].entries) == ("0.3.0", "2026-09-28", [("Three", "Три")])
    assert out.rstrip().endswith("[0.1.0]: https://github.com/alxndr-bnd/gtd/releases/tag/v0.1.0")
    assert "[Unreleased]: https://github.com/alxndr-bnd/gtd/compare/v0.3.0...HEAD" in out
    assert "[0.3.0]: https://github.com/alxndr-bnd/gtd/compare/v0.2.0...v0.3.0" in out
    assert C.release(out, "0.3.0", "2026-09-29") == out  # повторный запуск после упавшего гейта — без изменений
    assert C.notes(out, "0.3.0") == ("### Changed\n- Three\n\n"
                                      "Full history: [CHANGELOG.md](https://github.com/alxndr-bnd/gtd/blob/main/CHANGELOG.md)\n")


def test_release_refuses_without_entries():
    with pytest.raises(C.ChangelogError, match=r"no entry for 0\.3\.0.*\[Unreleased\]"):
        C.release(GOOD, "0.3.0", "2026-09-28")
    with pytest.raises(C.ChangelogError, match="not newer"):
        C.release(GOOD.replace("## [Unreleased]\n", "## [Unreleased]\n\n### Fixed\n- X\n  - RU: Х\n"), "0.1.5", "2026-09-28")


@pytest.mark.parametrize("release", C.released(C.parse(TEXT)), ids=lambda r: r.version)
def test_real_changelog_notes_are_english(release):
    """Заметки GitHub Release у каждой выпущенной версии (SERBITO-484: раньше — только у 0.16.0): все разделы
    по порядку, каждый английский пункт, ни одной кириллической буквы"""
    notes = C.notes(TEXT, release.version)
    first = next(name for name in C.SECTIONS if name in release.sections)
    assert notes.startswith(f"### {first}\n- ") and not CYR.search(notes)
    for en, _ in release.entries:
        assert f"\n- {en}\n" in notes


def test_docker_image_has_the_changelog():
    """Страница «Что нового» разбирает CHANGELOG.md при старте — без него в образе сервис не поднимется."""
    copy = re.search(r"^COPY app\.py .*$", (ROOT / "Dockerfile").read_text(), re.M).group(0).split()
    assert {"pages.py", "changelog.py", "CHANGELOG.md"} <= set(copy)


# ── Релизный скрипт во временном репозитории: свой origin (bare), gh — заглушка, пишущая аргументы ──

@pytest.fixture
def repo(tmp_path):
    work, remote, bin_ = tmp_path / "work", tmp_path / "remote.git", tmp_path / "bin"
    (work / "scripts").mkdir(parents=True)
    (work / "tests").mkdir()
    bin_.mkdir()
    shutil.copy(ROOT / "scripts" / "release_minor.sh", work / "scripts")
    shutil.copy(ROOT / "changelog.py", work)
    (work / "CHANGELOG.md").write_text(C.with_links(GOOD, C.parse(GOOD)), encoding="utf-8")
    (work / "tests" / "test_ok.py").write_text("def test_ok():\n    pass\n")
    gh = bin_ / "gh"
    gh.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{tmp_path}/gh-args"\n')
    gh.chmod(0o755)
    # Без унаследованных GIT_*: в git-хуке GIT_DIR и GIT_INDEX_FILE указывают на настоящий репозиторий,
    # и git init --bare перевёл бы его в core.bare = true (SERBITO-389)
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
           "PYTHON": sys.executable, "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTEST_XDIST_AUTO_NUM_WORKERS"] = "1"  # гейт идёт с -n auto; на одном тесте хватит одного воркера
    git("init", "-q", "--bare", str(remote), cwd=tmp_path, env=env)
    git("init", "-q", "-b", "main", cwd=work, env=env)
    for d in (work, remote):  # git пишет во временные репозитории, а не в настоящий
        git_dir = Path(git("rev-parse", "--absolute-git-dir", cwd=d, env=env).strip()).resolve()
        assert git_dir.is_relative_to(tmp_path.resolve()), git_dir
    git("add", ".", cwd=work, env=env)
    git("commit", "-qm", "init", cwd=work, env=env)
    git("tag", "v0.2.0", cwd=work, env=env)
    git("remote", "add", "origin", str(remote), cwd=work, env=env)
    git("push", "-qu", "origin", "main", "v0.2.0", cwd=work, env=env)

    def release():
        return subprocess.run(["bash", "scripts/release_minor.sh", "Release"], cwd=work, env=env,
                              capture_output=True, text=True, timeout=120)
    return work, remote, env, release, tmp_path / "gh-args"


def test_release_script_refuses_without_changelog_entry(repo):
    work, remote, env, release, gh_args = repo
    head = git("rev-parse", "HEAD", cwd=work, env=env)
    r = release()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "no entry for 0.3.0" in r.stderr and "## [Unreleased]" in r.stderr
    assert "==> pytest" not in r.stdout  # отказ до тестов, коммита и тега
    assert git("rev-parse", "HEAD", cwd=work, env=env) == head and "v0.3.0" not in git("tag", cwd=work, env=env)
    assert git("status", "--porcelain", "-uno", cwd=work, env=env) == "" and not gh_args.exists()


def test_release_script_dates_the_entry_and_uses_it_for_github_release(repo):
    work, remote, env, release, gh_args = repo
    log = work / "CHANGELOG.md"
    log.write_text(log.read_text().replace("## [Unreleased]\n", "## [Unreleased]\n\n### Added\n- Three\n  - RU: Три\n"))
    git("commit", "-qam", "entry", cwd=work, env=env)
    r = release()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 worker [1 item]" in r.stdout  # гейт параллельный: pytest-xdist (SERBITO-553)
    top = C.parse(log.read_text())[1]
    assert (top.version, top.date, top.entries) == ("0.3.0", date.today().isoformat(), [("Three", "Три")])
    assert git("status", "--porcelain", "-uno", cwd=work, env=env) == ""  # переименование — в релизном коммите
    assert "v0.3.0" in git("tag", cwd=remote, env=env).split()
    args = gh_args.read_text().splitlines()
    assert args[:6] == ["release", "create", "v0.3.0", "--verify-tag", "--title", "v0.3.0"]
    assert args[6] == "--notes" and "\n".join(args[7:]).startswith("### Added\n- Three") and "Три" not in gh_args.read_text()


# ── SERBITO-389: деплой тега без секции в CHANGELOG.md падает до сборки ──

def guard_step():
    """Шаг проверки из deploy.yml: его run-скрипт и номер среди шагов (pyyaml в зависимостях нет)."""
    lines = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8").splitlines()
    steps = [i for i, s in enumerate(lines) if s.startswith("      - ")]
    start = lines.index("      - name: CHANGELOG.md has this version")
    run = lines.index("        run: |", start)
    body = []
    for s in lines[run + 1:]:
        if s.strip() and not s.startswith(" " * 10):
            break
        body.append(s[10:])
    return "\n".join(body), steps, start, lines


def test_deploy_checks_the_changelog_right_after_checkout_before_the_build():
    _, steps, start, lines = guard_step()
    at = steps.index(start)
    # Между ними только выбор тега (SERBITO-401): проверяется тот тег, который деплоится
    assert lines[steps[at - 1]] == "      - name: Pick the release tag"
    assert "actions/checkout@" in lines[steps[at - 2]]
    build = next(i for i, s in enumerate(lines) if "docker build" in s or "docker/build-push-action" in s)
    assert start < build


@pytest.mark.parametrize("tag, ok", [("v0.2.0", True), ("v0.3.0", False), ("v0x2x0", False), ("v0.2", False)])
def test_deploy_refuses_a_tag_without_its_changelog_section(tmp_path, tag, ok):
    script = guard_step()[0]
    (tmp_path / "CHANGELOG.md").write_text(GOOD, encoding="utf-8")
    r = subprocess.run(["bash", "-e", "-c", script], cwd=tmp_path, capture_output=True, text=True,
                       env={"RELEASE_TAG": tag, "PATH": "/usr/bin:/bin"})
    assert (r.returncode == 0) == ok, r.stdout + r.stderr
    if not ok:
        assert f"::error file=CHANGELOG.md::No '## [{tag[1:]}]" in r.stdout
