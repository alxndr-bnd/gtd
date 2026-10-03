#!/usr/bin/env bash
# SERBITO-348: проверка заголовков безопасности после деплоя.
# Запрос один, без -L: проверяем ответ самого сервиса, а не того, куда он перенаправит.
# Нужны: HSTS, nosniff, запрет фрейма (X-Frame-Options или frame-ancestors в CSP),
# Referrer-Policy и CSP (боевая или report-only). Печатает имена недостающих; код 1, если чего-то нет.
#   scripts/check_security_headers.sh https://gtd.serbito.rs/
set -uo pipefail

url="${1:?usage: $0 <url>}"

if ! raw="$(curl -sS -D - -o /dev/null --max-time 30 "$url")"; then
  echo "Не удалось получить заголовки: $url" >&2
  exit 1
fi
# Имена заголовков — в нижний регистр, без \r; значения сравниваем без учёта регистра.
headers="$(printf '%s\n' "$raw" | tr -d '\r' | tr '[:upper:]' '[:lower:]')"

has() { printf '%s\n' "$headers" | grep -Eq "^$1:"; }

missing=()
has 'strict-transport-security' || missing+=("strict-transport-security")
printf '%s\n' "$headers" | grep -Eq '^x-content-type-options:[[:space:]]*nosniff' \
  || missing+=("x-content-type-options: nosniff")
if ! has 'x-frame-options' \
  && ! printf '%s\n' "$headers" | grep -Eq "^content-security-policy:.*frame-ancestors"; then
  missing+=("x-frame-options | content-security-policy frame-ancestors")
fi
has 'referrer-policy' || missing+=("referrer-policy")
has 'content-security-policy(-report-only)?' \
  || missing+=("content-security-policy | content-security-policy-report-only")

if [ "${#missing[@]}" -gt 0 ]; then
  echo "Нет заголовков безопасности на $url:"
  printf '  %s\n' "${missing[@]}"
  exit 1
fi
echo "Заголовки безопасности на месте: $url"
