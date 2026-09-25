"""parse_when: даты и время в тексте захвата. «Сейчас» — пятница 2026-09-25 14:00 по Белграду."""
from datetime import datetime, timedelta

import pytest

import app as A

NOW = datetime(2026, 9, 25, 14, 0, tzinfo=A.TZ)


def at(*args):
    return int(datetime(*args, tzinfo=A.TZ).timestamp())


@pytest.mark.parametrize("text, title, when", [
    ("через 2 часа проверить деплой", "проверить деплой", int((NOW + timedelta(hours=2)).timestamp())),
    ("через 30 минут чайник", "чайник", int((NOW + timedelta(minutes=30)).timestamp())),
    ("in 3 days pay rent", "pay rent", int((NOW + timedelta(days=3)).timestamp())),
    ("через 1 неделю ревью", "ревью", int((NOW + timedelta(weeks=1)).timestamp())),
    ("позвонить в банк завтра в 10:00", "позвонить в банк", at(2026, 9, 26, 10, 0)),
    ("послезавтра купить билеты", "купить билеты", at(2026, 9, 27, 9, 0)),
    ("tomorrow at 18:30 gym", "gym", at(2026, 9, 26, 18, 30)),
    ("отчёт в среду", "отчёт", at(2026, 9, 30, 9, 0)),
    ("отчёт в пятницу", "отчёт", at(2026, 10, 2, 9, 0)),  # сегодня пятница → следующая
    ("24.10 12:00 встреча", "встреча", at(2026, 10, 24, 12, 0)),
    ("01.09 день рождения", "день рождения", at(2027, 9, 1, 9, 0)),  # прошло → следующий год
    ("сдать 2026-10-24", "сдать", at(2026, 10, 24, 9, 0)),
    ("в 15:30 созвон", "созвон", at(2026, 9, 25, 15, 30)),
    ("в 09:00 зарядка", "зарядка", at(2026, 9, 26, 9, 0)),  # время прошло → завтра
    ("напомни мне про молоко завтра", "молоко", at(2026, 9, 26, 9, 0)),
    ("remind me to call mom today at 20:00", "call mom", at(2026, 9, 25, 20, 0)),
])
def test_parses_when(text, title, when):
    assert A.parse_when(text, NOW) == (title, when)


@pytest.mark.parametrize("text", ["просто мысль", "версия 1.2 готова", "31.02 несуществующая дата"])
def test_no_date(text):
    title, when = A.parse_when(text, NOW)
    assert when is None and title == text
