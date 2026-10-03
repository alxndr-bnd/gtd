"""Цепочка поставки (SERBITO-339): образ собирается только из замка с хешами на закреплённой базе.
Без этого любой деплой мог взять новый мажор зависимости или перезалитый python:3.14-slim без коммита."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
LOCKS = ("requirements.txt", "requirements-dev.txt")


def _from_python_minor():
    m = re.search(r"^FROM python:(\d+\.\d+)-slim@sha256:[0-9a-f]{64}$", DOCKERFILE, re.M)
    assert m, "FROM python:X.Y-slim без @sha256:<digest> — базу не закрепили"
    return m.group(1)


def test_base_image_pinned_by_digest():
    assert len(re.findall(r"^FROM ", DOCKERFILE, re.M)) == 1
    _from_python_minor()


def test_image_installs_only_from_the_lock():
    run = re.search(r"^RUN pip install .*$", DOCKERFILE, re.M)
    assert run, "нет pip install в Dockerfile"
    assert "--require-hashes" in run.group() and "-r requirements.txt" in run.group(), run.group()


def _pins(name):
    """Записи замка: имя==версия и хеши каждой."""
    text = (ROOT / name).read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=\S)", text)
    return {b.split("==")[0]: b for b in blocks if re.match(r"[A-Za-z0-9]", b)}, text


def test_locks_pin_every_package_with_hashes_for_the_image_python():
    minor = _from_python_minor()
    for name in LOCKS:
        pins, text = _pins(name)
        assert pins, name
        head = text.splitlines()[1]
        assert f"--python-version {minor}" in head and "--generate-hashes" in head, (name, head)
        for pkg, block in pins.items():
            assert re.match(r"[A-Za-z0-9._-]+(\[[^\]]+\])?==\S+", block), (name, block.splitlines()[0])
            assert "--hash=sha256:" in block, (name, pkg)


def test_dev_lock_uses_the_same_versions_as_the_image():
    prod, _ = _pins("requirements.txt")
    dev, _ = _pins("requirements-dev.txt")
    version = lambda block: block.split("==")[1].split()[0]  # noqa: E731
    assert prod.keys() <= dev.keys(), prod.keys() - dev.keys()
    assert {p: version(b) for p, b in prod.items()} == {p: version(dev[p]) for p in prod}


def test_image_copies_every_root_module():
    """Каждый .py из корня репо попадает в образ. Иначе контейнер падает на импорте (v0.29.0: забыли oauth.py)."""
    copy = re.search(r"^COPY app\.py .*$", DOCKERFILE, re.M).group(0).split()[1:-1]
    modules = sorted(p.name for p in ROOT.glob("*.py"))
    assert set(modules) <= set(copy), sorted(set(modules) - set(copy))
