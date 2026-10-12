#!/usr/bin/env python3
"""IndexNow (SERBITO-558): сообщает Bing, Yandex и другим поисковикам IndexNow об адресах из sitemap.xml.

Запускается шагом деплоя (.github/workflows/deploy.yml) после релиза: новая ревизия уже на трафике и отдаёт
ключ по адресу /<ключ>.txt. Ключ — имя файла static/<32 hex>.txt; ключи IndexNow публичные по замыслу.
Google IndexNow не читает — для него sitemap и Search Console.

Сбой сети или ответ не 200/202 — только предупреждение, код выхода 0: релиз от этого не ломается.

    python3 scripts/indexnow.py [--base https://gtd.serbito.rs] [--dry-run]

Только стандартная библиотека: в шаге деплоя зависимостей проекта нет."""
import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ENDPOINT = "https://api.indexnow.org/indexnow"
STATIC = Path(__file__).resolve().parent.parent / "static"
UA = "gtd-indexnow/1.0 (+https://github.com/alxndr-bnd/gtd)"


def key() -> str:
    found = sorted(p.stem for p in STATIC.glob("*.txt") if re.fullmatch(r"[0-9a-f]{32}", p.stem))
    if len(found) != 1:
        raise SystemExit(f"expected one static/<32 hex>.txt IndexNow key file, found {found}")
    return found[0]


def sitemap_urls(xml: str, host: str) -> list[str]:
    """Адреса из <loc>, только своего хоста: IndexNow отклоняет чужие."""
    return [u for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml) if urlparse(u).netloc == host]


def payload(base: str, k: str, urls: list[str]) -> dict:
    return {"host": urlparse(base).netloc, "key": k, "keyLocation": f"{base}/{k}.txt", "urlList": urls}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="https://gtd.serbito.rs")
    ap.add_argument("--dry-run", action="store_true", help="print the request, do not send it")
    a = ap.parse_args(argv)
    base = a.base.rstrip("/")
    k = key()
    try:
        req = urllib.request.Request(f"{base}/sitemap.xml", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            urls = sitemap_urls(r.read().decode("utf-8"), urlparse(base).netloc)
        body = payload(base, k, urls)
        if a.dry_run:
            print(json.dumps(body, indent=2))
            return 0
        req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"IndexNow: {len(urls)} URLs sent, HTTP {r.status}")
    except Exception as e:  # noqa: BLE001 — не фатально: релиз уже на проде
        print(f"::warning::IndexNow ping failed (not fatal): {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
