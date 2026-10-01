FROM python:3.14-slim
# Исправления безопасности Debian (например, openssl) выходят раньше, чем обновляется python:*-slim —
# без этого Trivy в деплое останавливает релиз на уже исправленных CVE.
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
# Манифесты вендоринга pip (pip/_vendor/bom.cdx.json, vendor.txt) декларируют его внутренний
# setuptools 70.3.0 — Trivy видит в нём HIGH CVE, хотя такой пакет не установлен. pip они не нужны.
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt && \
    find /usr/local/lib -type f \( -name 'bom.cdx.json' -o -name 'vendor.txt' \) -path '*/pip/_vendor/*' -delete
# CHANGELOG.md разбирается при старте: из него страница «Что нового» (/changes)
COPY app.py pages.py changelog.py CHANGELOG.md ./
COPY static static
# Не от root: приложению не нужно ничего писать в образ
RUN useradd --system --no-create-home app
USER app
ENV PORT=8080
CMD ["sh","-c","uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
