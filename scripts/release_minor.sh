#!/usr/bin/env bash
# Релиз gtd (по образцу serbito): CHANGELOG → гейт → коммит → следующий минорный тег vX.Y.0 → push → GitHub Release.
# Пуш тега запускает .github/workflows/deploy.yml — сборку и деплой в Cloud Run.
# Записи для людей пишут заранее в CHANGELOG.md под «## [Unreleased]»; скрипт превращает их в «## [X.Y.0] - дата»
# в этом же релизном коммите, а без записей релиз не делает (SERBITO-329, формат — changelog.py).
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"  # PYTHON — для теста скрипта во временном репозитории

msg="${1:-}"
if [[ -z "$msg" ]]; then
  echo "Usage: $0 \"commit message\" [new_file …]"
  echo "  Изменения в отслеживаемых файлах добавляются сами (git add -u)."
  echo "  Новые файлы — только явно, чтобы в релиз не уехал черновой мусор."
  exit 1
fi
shift  # дальше в $@ — явно перечисленные новые файлы (может быть пусто)

branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$branch" != "main" ]]; then
  echo "Релиз только из main (сейчас: $branch)" >&2
  exit 1
fi

latest_tag="$(git tag --list 'v*.*.*' --sort=-v:refname | head -n 1)"
if [[ -z "$latest_tag" ]]; then
  next_tag="v0.1.0"
else
  version="${latest_tag#v}"
  IFS='.' read -r major minor patch <<< "$version"
  next_tag="v${major}.$((minor + 1)).0"
fi

# --- CHANGELOG: [Unreleased] → [X.Y.0] - сегодня. Записей нет — отказ до тестов, коммита и тега ---
echo "==> CHANGELOG.md: ${next_tag#v}"
if ! "$PY" changelog.py release "${next_tag#v}"; then
  echo "Релиз $next_tag не сделан: допишите в CHANGELOG.md, что получат пользователи (README → Releasing)" >&2
  exit 1
fi
notes="$("$PY" changelog.py notes "${next_tag#v}")"

# --- гейт: тесты (локальный Postgres, временная база) и браузерный смоук SPA в Chromium.
# Нет Chromium — тест падает с подсказкой: .venv/bin/playwright install chromium ---
echo "==> pytest"
"$PY" -m pytest

git add -u
if [[ $# -gt 0 ]]; then
  git add -- "$@"
fi

if git diff --cached --quiet; then
  git commit --allow-empty -m "$msg"
else
  git commit -m "$msg"
fi

git tag "$next_tag"
git push
git push origin "$next_tag"

echo "Released $next_tag — деплой: https://github.com/alxndr-bnd/gtd/actions"

# --- GitHub Release: текст — английская часть записи версии из CHANGELOG.md. Не фатально: тег и деплой уже ушли ---
retry="gh release create $next_tag --verify-tag --title $next_tag --notes \"\$($PY changelog.py notes ${next_tag#v})\""
if ! command -v gh >/dev/null 2>&1; then
  echo "WARNING: gh не найден — GitHub Release для $next_tag не создан: $retry" >&2
elif ! gh release create "$next_tag" --verify-tag --title "$next_tag" --notes "$notes"; then
  echo "WARNING: GitHub Release для $next_tag не создан — повторить: $retry" >&2
fi
exit 0
