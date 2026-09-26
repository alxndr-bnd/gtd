#!/usr/bin/env bash
# Релиз gtd (по образцу serbito): гейт → коммит → следующий минорный тег vX.Y.0 → push → GitHub Release.
# Пуш тега запускает .github/workflows/deploy.yml — сборку и деплой в Cloud Run.
set -euo pipefail
cd "$(dirname "$0")/.."

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

# --- гейт: тесты (локальный Postgres, временная база) ---
echo "==> pytest"
.venv/bin/python -m pytest

git add -u
if [[ $# -gt 0 ]]; then
  git add -- "$@"
fi

if git diff --cached --quiet; then
  git commit --allow-empty -m "$msg"
else
  git commit -m "$msg"
fi

latest_tag="$(git tag --list 'v*.*.*' --sort=-v:refname | head -n 1)"
if [[ -z "$latest_tag" ]]; then
  next_tag="v0.1.0"
else
  version="${latest_tag#v}"
  IFS='.' read -r major minor patch <<< "$version"
  next_tag="v${major}.$((minor + 1)).0"
fi

git tag "$next_tag"
git push
git push origin "$next_tag"

echo "Released $next_tag — деплой: https://github.com/alxndr-bnd/gtd/actions"

# --- GitHub Release: заметки из коммитов/PR с прошлого тега. Не фатально: тег и деплой уже ушли ---
if ! command -v gh >/dev/null 2>&1; then
  echo "WARNING: gh не найден — GitHub Release для $next_tag не создан (gh release create $next_tag --generate-notes)" >&2
elif ! gh release create "$next_tag" --verify-tag --title "$next_tag" --generate-notes; then
  echo "WARNING: GitHub Release для $next_tag не создан — повторить: gh release create $next_tag --verify-tag --generate-notes" >&2
fi
exit 0
