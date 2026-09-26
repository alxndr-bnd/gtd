#!/usr/bin/env bash
# Разовая настройка GCP под деплой gtd из GitHub Actions (как у javi / tickets-booking).
# Идемпотентен: повторный запуск ничего не ломает и не перезаписывает существующие секреты.
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT=serbito
REGION=europe-west1
REPO=alxndr-bnd/gtd
POOL=github-pool
PROVIDER=github
DEPLOYER=gtd-deployer@$PROJECT.iam.gserviceaccount.com
RUNTIME=gtd-run@$PROJECT.iam.gserviceaccount.com
g() { gcloud --project "$PROJECT" --quiet "$@"; }
PROJECT_NUMBER="$(g projects describe "$PROJECT" --format='value(projectNumber)')"

echo "==> Artifact Registry: gtd"
g artifacts repositories describe gtd --location "$REGION" >/dev/null 2>&1 \
  || g artifacts repositories create gtd --repository-format=docker --location "$REGION"

echo "==> Сервисные аккаунты: деплоер и рантайм"
for sa in gtd-deployer gtd-run; do
  g iam service-accounts describe "$sa@$PROJECT.iam.gserviceaccount.com" >/dev/null 2>&1 \
    || g iam service-accounts create "$sa" --display-name "$sa"
done

echo "==> Роли деплоера: Cloud Run, пуш в свой реестр, запуск от имени рантайм-SA"
g projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$DEPLOYER" \
  --role roles/run.admin --condition=None >/dev/null
g artifacts repositories add-iam-policy-binding gtd --location "$REGION" \
  --member "serviceAccount:$DEPLOYER" --role roles/artifactregistry.writer >/dev/null
g iam service-accounts add-iam-policy-binding "$RUNTIME" \
  --member "serviceAccount:$DEPLOYER" --role roles/iam.serviceAccountUser >/dev/null

echo "==> Роли рантайма: Cloud SQL"
g projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$RUNTIME" \
  --role roles/cloudsql.client --condition=None >/dev/null

echo "==> Секреты"
for s in gtd-database-url gtd-telegram-bot-token gtd-sentry-dsn gtd-cron-secret; do
  g secrets describe "$s" >/dev/null 2>&1 || g secrets create "$s" --replication-policy=automatic >/dev/null
done
has_value() { g secrets versions list "$1" --format='value(state)' | grep -qi enabled; }
put_value() { printf '%s' "$2" | g secrets versions add "$1" --data-file=- >/dev/null; echo "    записан $1"; }

if ! has_value gtd-database-url; then
  # Пароль берём из локального .env (там он уже в URL-кодировке), иначе спрашиваем
  pw="$(sed -nE 's#^DATABASE_URL(_PROD)?=postgresql://gtd:([^@]*)@.*#\2#p' .env 2>/dev/null | head -1 || true)"
  if [[ -z "$pw" ]]; then read -rsp "Пароль пользователя gtd в Cloud SQL: " pw || true; echo; fi
  [[ -n "$pw" ]] || { echo "Без пароля БД деплой невозможен" >&2; exit 1; }
  put_value gtd-database-url "postgresql://gtd:$pw@/gtd?host=/cloudsql/$PROJECT:$REGION:serbitodb"
fi
if ! has_value gtd-telegram-bot-token; then
  read -rsp "Токен бота gtd от @BotFather (Enter — пропустить, бот будет выключен): " tok || tok=""; echo
  if [[ -n "$tok" ]]; then
    put_value gtd-telegram-bot-token "$tok"
  else
    echo "    токена нет — деплой пойдёт без бота; появится токен — запусти скрипт ещё раз"
  fi
fi
# GOOGLE_CLIENT_ID и EMAIL_HOST_PASSWORD — общие с serbito, доступ только на чтение этих двух
if ! has_value gtd-sentry-dsn; then
  read -rsp "DSN проекта gtd в Sentry (nohandoff): " dsn || dsn=""; echo
  [[ -n "$dsn" ]] || { echo "Без DSN деплой не пройдёт: секрет подключён в workflow" >&2; exit 1; }
  put_value gtd-sentry-dsn "$dsn"
fi
has_value gtd-cron-secret || put_value gtd-cron-secret "$(openssl rand -hex 32)"
for s in gtd-database-url gtd-telegram-bot-token gtd-sentry-dsn gtd-cron-secret GOOGLE_CLIENT_ID EMAIL_HOST_PASSWORD; do
  g secrets add-iam-policy-binding "$s" --member "serviceAccount:$RUNTIME" \
    --role roles/secretmanager.secretAccessor >/dev/null
done
# Деплоеру — только метаданные секрета бота (не значение): чтобы видеть, задан ли токен
g secrets add-iam-policy-binding gtd-telegram-bot-token --member "serviceAccount:$DEPLOYER" \
  --role roles/secretmanager.viewer >/dev/null

echo "==> WIF: пускаем $REPO (только его) к деплоеру"
cond="$(g iam workload-identity-pools providers describe "$PROVIDER" --workload-identity-pool "$POOL" \
  --location global --format='value(attributeCondition)')"
if [[ "$cond" != *"'$REPO'"* ]]; then
  new="$(printf '%s' "$cond" | sed "s#]#,'$REPO']#")"
  echo "    было:  $cond"
  echo "    стало: $new"
  g iam workload-identity-pools providers update-oidc "$PROVIDER" --workload-identity-pool "$POOL" \
    --location global --attribute-condition "$new" >/dev/null
fi
g iam service-accounts add-iam-policy-binding "$DEPLOYER" --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$POOL/attribute.repository/$REPO" \
  >/dev/null

echo "==> Cloud Scheduler: будильник напоминаний раз в минуту"
cron_args=(--location "$REGION" --schedule "* * * * *" --time-zone "Europe/Belgrade"
  --uri "https://gtd.serbito.rs/tasks/reminders" --http-method POST
  --headers "X-Cron-Secret=$(g secrets versions access latest --secret gtd-cron-secret)"
  --attempt-deadline 60s)
if g scheduler jobs describe gtd-reminders --location "$REGION" >/dev/null 2>&1; then
  g scheduler jobs update http gtd-reminders "${cron_args[@]/--headers/--update-headers}" >/dev/null
else
  g scheduler jobs create http gtd-reminders "${cron_args[@]}" >/dev/null
fi

echo "Готово. Первый релиз: scripts/release_minor.sh \"…\"; после него — домен (см. README)."
