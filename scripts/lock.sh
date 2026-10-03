#!/usr/bin/env bash
# Замок зависимостей (SERBITO-339): requirements*.in → requirements*.txt с точными версиями и хешами.
# Образ ставит requirements.txt с --require-hashes, локально и в CI — requirements-dev.txt.
#   scripts/lock.sh                           — после правки .in: добавить/убрать пакет, сдвинуть нижнюю границу
#   scripts/lock.sh --upgrade                 — поднять всё до свежих версий
#   scripts/lock.sh --upgrade-package fastapi — поднять один пакет
# Без --upgrade uv оставляет версии, уже записанные в .txt. CI запускает скрипт без аргументов и падает,
# если замок разошёлся с .in. Те же флаги читает Dependabot (ecosystem uv) из шапки .txt — их не менять.
# --universal: один замок для macOS разработчика и Linux образа; Python — как в FROM Dockerfile.
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v uv >/dev/null 2>&1; then
  echo "Нужен uv: brew install uv (или https://docs.astral.sh/uv/)" >&2
  exit 1
fi
compile() { uv pip compile --quiet --universal --generate-hashes --python-version 3.14 "$1" -o "$2" "${@:3}"; }
# Порядок важен: requirements-dev.in берёт версии из готового requirements.txt (-c)
compile requirements.in requirements.txt "$@"
compile requirements-dev.in requirements-dev.txt "$@"
