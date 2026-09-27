""".env.example — публичный шаблон настроек (SERBITO-315): в нём есть каждая переменная, которую читает
приложение, и нет ничего от инфраструктуры владельца (IP базы, логин Brevo)."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = (ROOT / ".env.example").read_text(encoding="utf-8")
APP_MODULES = ("app.py", "pages.py")


def _is_os_environ(node):
    return isinstance(node, ast.Attribute) and node.attr == "environ" \
        and isinstance(node.value, ast.Name) and node.value.id == "os"


def env_reads(path):
    """Имена переменных из os.getenv("X"), os.environ.get("X") и os.environ["X"]."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        key = None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
            f = node.func
            if (f.attr == "getenv" and isinstance(f.value, ast.Name) and f.value.id == "os") \
                    or (f.attr == "get" and _is_os_environ(f.value)):
                key = node.args[0]
        elif isinstance(node, ast.Subscript) and _is_os_environ(node.value):
            key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            names.add(key.value)
    return names


def template_vars():
    """Переменные шаблона: и заданные (X=…), и закомментированные (# X=…)."""
    return set(re.findall(r"^(?:#\s*)?([A-Z][A-Z0-9_]*)=", TEMPLATE, re.M))


def test_every_env_var_is_in_template():
    read = set().union(*(env_reads(ROOT / m) for m in APP_MODULES))
    assert {"DATABASE_URL", "TELEGRAM_BOT_TOKEN", "SMTP_PASSWORD", "SENTRY_DSN"} <= read  # парсер не ослеп
    assert read - template_vars() == set()


def test_template_has_no_owner_infra():
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", TEMPLATE), "IP-адрес в .env.example"
    assert "@smtp-brevo.com" not in TEMPLATE
    assert "DATABASE_URL_PROD" not in TEMPLATE  # боевая база — только в личном .env владельца


def test_template_database_is_local():
    # README обещает: .env по умолчанию смотрит в локальную базу
    urls = re.findall(r"^DATABASE_URL=(.*)$", TEMPLATE, re.M)
    assert len(urls) == 1 and re.match(r"postgresql://(localhost|127\.0\.0\.1)[:/]", urls[0])
