"""CHANGELOG.md — история версий для людей (SERBITO-329): Keep a Changelog, у каждого пункта английский текст
и русский перевод под ним. Один разборщик на всё: страница «Что нового» (/changes, pages.py), тесты
(tests/test_changelog.py) и релизный скрипт (scripts/release_minor.sh):

    python changelog.py release X.Y.Z [YYYY-MM-DD]  — [Unreleased] → [X.Y.Z] - дата (нет записей — отказ)
    python changelog.py notes X.Y.Z                 — английский текст версии для GitHub Release
    python changelog.py check                       — только разобрать и проверить формат

Формат (он же описан в шапке CHANGELOG.md). Шапка до первого «## » — свободный текст, дальше строго:
    ## [Unreleased]                  — первым, сюда пишут до релиза (может быть пустым)
    ## [0.16.0] - 2026-09-27         — релизы, новые сверху
    ### Added | Changed | Fixed | Security
    - English text                   — пункт, одна строка
      - RU: Русский текст            — вложенный пункт сразу под ним
    [0.16.0]: https://github.com/…   — ссылки сравнения в самом конце (их пересобирает release)
Любая другая строка — ошибка разбора с номером строки: формат не расползается молча."""
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PATH = Path(__file__).resolve().parent / "CHANGELOG.md"
REPO_URL = "https://github.com/alxndr-bnd/gtd"
UNRELEASED = "Unreleased"
SECTIONS = ("Added", "Changed", "Fixed", "Security")  # порядок вывода на странице и в заметках релиза

RE_VERSION = re.compile(r"## \[(Unreleased|\d+\.\d+\.\d+)\](?: - (\d{4}-\d{2}-\d{2}))?")
RE_SECTION = re.compile(r"### (.+)")
RE_EN = re.compile(r"- (\S.*)")
RE_RU = re.compile(r"  - RU: (\S.*)")
RE_LINK = re.compile(r"\[(Unreleased|\d+\.\d+\.\d+)\]: (\S+)")


class ChangelogError(ValueError):
    pass


@dataclass
class Release:
    version: str                      # "0.16.0" или "Unreleased"
    date: str | None                  # "2026-09-27"; у Unreleased — None
    sections: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # "Added" → [(en, ru)]

    @property
    def entries(self) -> list[tuple[str, str]]:
        return [e for name in SECTIONS for e in self.sections.get(name, [])]


def vkey(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in version.split("."))


def parse(text: str) -> list[Release]:
    """Версии сверху вниз, как в файле. Ошибка формата — ChangelogError с номером строки."""
    lines = text.splitlines()
    start = next((i for i, s in enumerate(lines) if s.startswith("## ")), None)
    if start is None:
        raise ChangelogError("CHANGELOG.md: no '## [version]' headings")
    releases: list[Release] = []
    sec, links = None, False
    for n, line in enumerate(lines[start:], start + 1):
        def fail(why):
            raise ChangelogError(f"CHANGELOG.md:{n}: {why}: {line!r}")
        if not line.strip():
            continue
        if RE_LINK.fullmatch(line):
            links = True
            continue
        if links:
            fail("only link definitions may follow the link definitions")
        if m := RE_VERSION.fullmatch(line):
            ver, day = m.groups()
            if ver == UNRELEASED:
                if day or releases:
                    fail("[Unreleased] goes first and has no date")
            else:
                if not day:
                    fail("a released version needs a date: '## [X.Y.Z] - YYYY-MM-DD'")
                try:
                    date.fromisoformat(day)
                except ValueError:
                    fail("bad date")
            releases.append(Release(ver, day))
            sec = None
            continue
        if not releases:
            fail("expected a version heading")
        if m := RE_SECTION.fullmatch(line):
            name = m.group(1)
            if name not in SECTIONS:
                fail(f"unknown section, use one of {', '.join(SECTIONS)}")
            if name in releases[-1].sections:
                fail("duplicate section")
            sec = releases[-1].sections[name] = []
            continue
        if m := RE_RU.fullmatch(line):
            if not sec or sec[-1][1] is not None:
                fail("a '  - RU:' line must directly follow its English '- ' line")
            sec[-1] = (sec[-1][0], m.group(1))
            continue
        if m := RE_EN.fullmatch(line):
            if sec is None:
                fail("an entry must be under '### Added/Changed/Fixed/Security'")
            if sec and sec[-1][1] is None:
                fail("the previous entry has no '  - RU:' line")
            sec.append((m.group(1), None))
            continue
        fail("unexpected line")
    for r in releases:
        for name, items in r.sections.items():
            if not items:
                raise ChangelogError(f"CHANGELOG.md: [{r.version}] ### {name} is empty")
            if items[-1][1] is None:
                raise ChangelogError(f"CHANGELOG.md: [{r.version}] '{items[-1][0]}' has no '  - RU:' line")
    done = [r for r in releases if r.version != UNRELEASED]
    for r in done:
        if not r.entries:
            raise ChangelogError(f"CHANGELOG.md: [{r.version}] has no entries")
    for newer, older in zip(done, done[1:]):
        if vkey(newer.version) <= vkey(older.version) or newer.date < older.date:
            raise ChangelogError(f"CHANGELOG.md: [{newer.version}] must be newer than [{older.version}] below it")
    return releases


def load(path: Path = PATH) -> list[Release]:
    return parse(path.read_text(encoding="utf-8"))


def released(releases: list[Release]) -> list[Release]:
    return [r for r in releases if r.version != UNRELEASED]


def link_lines(releases: list[Release]) -> list[str]:
    """Ссылки сравнения по Keep a Changelog: [Unreleased] — от последнего тега до HEAD, версия — от предыдущей."""
    done = [r.version for r in released(releases)]
    out = [f"[{UNRELEASED}]: {REPO_URL}/compare/v{done[0]}...HEAD"] if done else []
    for ver, prev in zip(done, done[1:] + [None]):
        out.append(f"[{ver}]: {REPO_URL}/compare/v{prev}...v{ver}" if prev else f"[{ver}]: {REPO_URL}/releases/tag/v{ver}")
    return out


def with_links(text: str, releases: list[Release]) -> str:
    """Текст с пересобранным блоком ссылок в конце."""
    lines = text.rstrip("\n").splitlines()
    while lines and (not lines[-1].strip() or RE_LINK.fullmatch(lines[-1])):
        lines.pop()
    return "\n".join(lines + [""] + link_lines(releases)) + "\n"


def release(text: str, version: str, day: str) -> str:
    """Выпуск версии: записи из [Unreleased] становятся «## [version] - day», сверху — новый пустой [Unreleased].
    Версия уже есть в файле (повторный запуск после упавшего гейта) — текст как есть.
    Записей нет — ChangelogError с подсказкой, что дописать."""
    releases = parse(text)
    if any(r.version == version for r in releases):
        return text
    done = released(releases)
    if done and vkey(version) <= vkey(done[0].version):
        raise ChangelogError(f"CHANGELOG.md: {version} is not newer than the latest entry [{done[0].version}]")
    top = releases[0] if releases[0].version == UNRELEASED else None
    if not top or not top.entries:
        raise ChangelogError(
            f"CHANGELOG.md has no entry for {version}. Add what users get under '## [{UNRELEASED}]' "
            f"(### Added / Changed / Fixed / Security: an English '- ' line and a '  - RU: ' line under it), "
            f"then run the release again — it turns [{UNRELEASED}] into [{version}] - {day}.")
    date.fromisoformat(day)
    head = f"## [{UNRELEASED}]"
    text = text.replace(head + "\n", f"{head}\n\n## [{version}] - {day}\n", 1)
    return with_links(text, parse(text))


def notes(text: str, version: str) -> str:
    """Тело GitHub Release: английский текст версии (русский — на /changes и в CHANGELOG.md)."""
    r = next((r for r in parse(text) if r.version == version), None)
    if not r or not r.entries:
        raise ChangelogError(f"CHANGELOG.md has no entries for {version}")
    out = []
    for name in SECTIONS:
        if name in r.sections:
            out += [f"### {name}", *(f"- {en}" for en, _ in r.sections[name]), ""]
    out.append(f"Full history: [CHANGELOG.md]({REPO_URL}/blob/main/CHANGELOG.md)")
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    cmd, *args = argv or ["check"]
    try:
        text = PATH.read_text(encoding="utf-8")
        if cmd == "check" and not args:
            parse(text)
        elif cmd == "release" and len(args) in (1, 2):
            new = release(text, args[0], args[1] if len(args) == 2 else date.today().isoformat())
            if new != text:
                PATH.write_text(new, encoding="utf-8")
        elif cmd == "notes" and len(args) == 1:
            sys.stdout.write(notes(text, args[0]))
        else:
            print(__doc__.split("\n\n")[1], file=sys.stderr)
            return 2
    except ChangelogError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
