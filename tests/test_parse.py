"""parse_when: даты и время в тексте захвата. «Сейчас» — пятница 2026-09-25 14:00 по Белграду.
Русские случаи (в том числе «даты нет») — в таблице RU_GOLDEN ниже."""
from datetime import datetime, timedelta

import pytest

import app as A

NOW = datetime(2026, 9, 25, 14, 0, tzinfo=A.TZ)


def at(*args):
    return int(datetime(*args, tzinfo=A.TZ).timestamp())


# ── Английский захват (SERBITO-259) ──
@pytest.mark.parametrize("text, title, when", [
    ("call mom tomorrow at 10:00", "call mom", at(2026, 9, 26, 10, 0)),
    ("tomorrow 10am call mom", "call mom", at(2026, 9, 26, 10, 0)),
    ("call mom tomorrow at 10 AM", "call mom", at(2026, 9, 26, 10, 0)),
    ("at 3pm standup", "standup", at(2026, 9, 25, 15, 0)),
    ("at 3 p.m. standup", "standup", at(2026, 9, 25, 15, 0)),
    ("at 11am standup", "standup", at(2026, 9, 26, 11, 0)),  # время прошло → завтра
    ("read 9:30pm", "read", at(2026, 9, 25, 21, 30)),
    ("backup 12am", "backup", at(2026, 9, 26, 0, 0)),  # 12am — полночь
    ("lunch 12pm tomorrow", "lunch", at(2026, 9, 26, 12, 0)),  # 12pm — полдень
    ("today at 8pm call", "call", at(2026, 9, 25, 20, 0)),
    ("report on friday", "report", at(2026, 10, 2, 9, 0)),  # сегодня пятница → следующая
    ("on monday at 4pm dentist", "dentist", at(2026, 9, 28, 16, 0)),
    ("next monday plan the sprint", "plan the sprint", at(2026, 9, 28, 9, 0)),
    ("gym this saturday", "gym", at(2026, 9, 26, 9, 0)),
    ("24 oct 12:00 meeting", "meeting", at(2026, 10, 24, 12, 0)),
    ("meeting on 24 October", "meeting", at(2026, 10, 24, 9, 0)),
    ("oct 24 meeting 3pm", "meeting", at(2026, 10, 24, 15, 0)),
    ("trip October 24, 2027", "trip", at(2027, 10, 24, 9, 0)),
    ("party 1st of january", "party", at(2027, 1, 1, 9, 0)),  # прошло → следующий год
    ("pay rent on 2026-10-24", "pay rent", at(2026, 10, 24, 9, 0)),
    ("2026-10-24 10:30 defense", "defense", at(2026, 10, 24, 10, 30)),
    ("in 2 hours check the deploy", "check the deploy", int((NOW + timedelta(hours=2)).timestamp())),
    ("in 30 min tea", "tea", int((NOW + timedelta(minutes=30)).timestamp())),
    ("in 3 hrs call back", "call back", int((NOW + timedelta(hours=3)).timestamp())),
    ("review in 1 week", "review", int((NOW + timedelta(weeks=1)).timestamp())),
    ("day after tomorrow buy tickets", "buy tickets", at(2026, 9, 27, 9, 0)),
    ("remind me to call mom tomorrow 10am", "call mom", at(2026, 9, 26, 10, 0)),
    ("remind me to call mom today at 20:00", "call mom", at(2026, 9, 25, 20, 0)),
    ("in 3 days pay rent", "pay rent", int((NOW + timedelta(days=3)).timestamp())),
    ("tomorrow at 18:30 gym", "gym", at(2026, 9, 26, 18, 30)),
])
def test_parses_when_english(text, title, when):
    assert A.parse_when(text, NOW) == (title, when)


@pytest.mark.parametrize("text", ["buy 2 apples", "octopus 24 legs", "march forward", "version 3.2 ready",
                                  "31 feb party", "13pm nonsense"])
def test_no_date_english(text):
    title, when = A.parse_when(text, NOW)
    assert when is None and title == text


# ── Русский захват не изменился: ответы parse_when до SERBITO-259, зафиксированные один в один
# (включая известную странность «и в 8:30»; «В звонок» исправлен в SERBITO-286)
RU_GOLDEN = [
    ('через 2 часа проверить деплой', 'проверить деплой', at(2026, 9, 25, 16, 0)),
    ('через 30 минут чайник', 'чайник', at(2026, 9, 25, 14, 30)),
    ('через 1 неделю ревью', 'ревью', at(2026, 10, 2, 14, 0)),
    ('через 3 дня оплатить', 'оплатить', at(2026, 9, 28, 14, 0)),
    ('через 5 мин выйти', 'выйти', at(2026, 9, 25, 14, 5)),
    ('через 2 ч созвон', 'созвон', at(2026, 9, 25, 16, 0)),
    ('через 1 день', '', at(2026, 9, 26, 14, 0)),
    ('через 10 минут', '', at(2026, 9, 25, 14, 10)),
    ('проверить через 2 недели почту', 'проверить почту', at(2026, 10, 9, 14, 0)),
    ('позвонить в банк завтра в 10:00', 'позвонить в банк', at(2026, 9, 26, 10, 0)),
    ('послезавтра купить билеты', 'купить билеты', at(2026, 9, 27, 9, 0)),
    ('сегодня в 18:00 спорт', 'спорт', at(2026, 9, 25, 18, 0)),
    ('сегодня купить хлеб', 'купить хлеб', at(2026, 9, 25, 9, 0)),
    ('завтра', '', at(2026, 9, 26, 9, 0)),
    ('завтра в 9:05 врач', 'врач', at(2026, 9, 26, 9, 5)),
    ('к 17:00 отчёт', 'отчёт', at(2026, 9, 25, 17, 0)),
    ('в 15:30 созвон', 'созвон', at(2026, 9, 25, 15, 30)),
    ('в 09:00 зарядка', 'зарядка', at(2026, 9, 26, 9, 0)),
    ('23:59 спать', 'спать', at(2026, 9, 25, 23, 59)),
    ('24:00 ерунда', '24:00 ерунда', None),
    ('12:60 ерунда', '12:60 ерунда', None),
    ('отчёт в среду', 'отчёт', at(2026, 9, 30, 9, 0)),
    ('отчёт в пятницу', 'отчёт', at(2026, 10, 2, 9, 0)),
    ('во вторник встреча', 'встреча', at(2026, 9, 29, 9, 0)),
    ('в понедельник', '', at(2026, 9, 28, 9, 0)),
    ('в воскресенье отдых', 'отдых', at(2026, 9, 27, 9, 0)),
    ('на субботу дача', 'дача', at(2026, 9, 26, 9, 0)),
    ('в четверг в 19:00 кино', 'кино', at(2026, 10, 1, 19, 0)),
    ('в среду в 8:15 бег', 'бег', at(2026, 9, 30, 8, 15)),
    ('суббота уборка', 'уборка', at(2026, 9, 26, 9, 0)),
    ('пятница вечер', 'вечер', at(2026, 10, 2, 9, 0)),
    ('24.10 12:00 встреча', 'встреча', at(2026, 10, 24, 12, 0)),
    ('01.09 день рождения', 'день рождения', at(2027, 9, 1, 9, 0)),
    ('1.02 оплата', 'оплата', at(2027, 2, 1, 9, 0)),
    ('31.12.2026 праздник', 'праздник', at(2026, 12, 31, 9, 0)),
    ('15.03.2027 отпуск', 'отпуск', at(2027, 3, 15, 9, 0)),
    ('сдать 2026-10-24', 'сдать', at(2026, 10, 24, 9, 0)),
    ('2026-10-24 10:30 защита', 'защита', at(2026, 10, 24, 10, 30)),
    ('2026-02-30 плохая дата', '2026-02-30 плохая дата', None),
    ('31.02 несуществующая дата', '31.02 несуществующая дата', None),
    ('версия 1.2 готова', 'версия 1.2 готова', None),
    ('просто мысль', 'просто мысль', None),
    ('напомни мне про молоко завтра', 'молоко', at(2026, 9, 26, 9, 0)),
    ('напомнить о встрече в пятницу', 'встрече', at(2026, 10, 2, 9, 0)),
    ('напомни что надо позвонить завтра в 10:00', 'надо позвонить', at(2026, 9, 26, 10, 0)),
    ('Напомни мне, про кота через 2 часа', 'кота', at(2026, 9, 25, 16, 0)),
    ('купить 2 кг яблок', 'купить 2 кг яблок', None),
    ('встреча в 10', 'встреча в 10', None),
    ('отчёт #Клиент_X @работа', 'отчёт #Клиент_X @работа', None),
    ('позвонить маме завтра в 10:00 @телефон', 'позвонить маме @телефон', at(2026, 9, 26, 10, 0)),
    ('ПОСЛЕЗАВТРА в 11:00 Дантист', 'Дантист', at(2026, 9, 27, 11, 0)),
    ('Завтра В 10:00 звонок', 'звонок', at(2026, 9, 26, 10, 0)),  # SERBITO-286: предлог без учёта регистра
    ('в Пятницу отчёт', 'отчёт', at(2026, 10, 2, 9, 0)),
    ('через 2 Часа', '', at(2026, 9, 25, 16, 0)),
    ('полить цветы — завтра, в 8:00', 'полить цветы', at(2026, 9, 26, 8, 0)),
    ('курс 1.25 валюты', 'курс 1.25 валюты', None),
    ('в 7:30 и в 8:30', 'и в 8:30', at(2026, 9, 26, 7, 30)),
    ('дважды 25.10 и 26.10', 'дважды и 26.10', at(2026, 10, 25, 9, 0)),
]


@pytest.mark.parametrize("text, title, when", RU_GOLDEN)
def test_russian_parsing_unchanged(text, title, when):
    assert A.parse_when(text, NOW) == (title, when)


# ── SERBITO-286: число через точку — дата, только если похоже на дату ──
@pytest.mark.parametrize("text", ["версия 1.05 готова", "курс 1.10 валюты", "обновить до версии 2.10",
                                  "release 1.05 notes", "цена 3.50 за штуку"])
def test_decimal_is_not_date(text):
    assert A.parse_when(text, NOW) == (text, None)


@pytest.mark.parametrize("text, title, when", [
    ("1.10 встреча", "встреча", at(2026, 10, 1, 9, 0)),  # начало текста
    ("сдать отчёт до 5.10", "сдать отчёт до", at(2026, 10, 5, 9, 0)),
    ("к 5.10 отчёт", "к отчёт", at(2026, 10, 5, 9, 0)),
    ("встреча на 7.10", "встреча на", at(2026, 10, 7, 9, 0)),
    ("pay rent by 1.10", "pay rent by", at(2026, 10, 1, 9, 0)),
    ("встреча 1.10 12:00", "встреча", at(2026, 10, 1, 12, 0)),
    ("встреча 1.10 в 12:00", "встреча", at(2026, 10, 1, 12, 0)),
    ("meeting 1.10 at 3pm", "meeting", at(2026, 10, 1, 15, 0)),
    ("отпуск 5.10.2026", "отпуск", at(2026, 10, 5, 9, 0)),
    ("встреча 05.10", "встреча", at(2026, 10, 5, 9, 0)),  # двузначный день — всегда дата
    ("версия 1.05 и 25.10 релиз", "версия 1.05 и релиз", at(2026, 10, 25, 9, 0)),
    ("Звонок В 16:00", "Звонок", at(2026, 9, 25, 16, 0)),
    ("К 17:00 отчёт", "отчёт", at(2026, 9, 25, 17, 0)),
    ("call AT 18:30", "call", at(2026, 9, 25, 18, 30)),
])
def test_date_with_marker(text, title, when):
    assert A.parse_when(text, NOW) == (title, when)


# ── SERBITO-582: срок рядом с датой. Явная дата (день + месяц) важнее «через N …» и «за N …»;
# «за N дней до <даты>» / «N days before <date>» — напоминание раньше даты на этот срок ──
@pytest.mark.parametrize("text, title, when", [
    # Пример из тикета: «сейчас» — четверг 2026-10-08 15:00, раньше давал завтра 10:00
    ("SERBITO-578: сравнить результат рекламы за 2 недели, затем включить Google tag gateway 22 октября 10:00",
     "SERBITO-578: сравнить результат рекламы за 2 недели, затем включить Google tag gateway",
     at(2026, 10, 22, 10, 0)),
])
def test_ticket_example(text, title, when):
    assert A.parse_when(text, datetime(2026, 10, 8, 15, 0, tzinfo=A.TZ)) == (title, when)


@pytest.mark.parametrize("text, title, when", [
    # Русский месяц словом
    ("сдать отчёт 5 ноября", "сдать отчёт", at(2026, 11, 5, 9, 0)),
    ("22 октября в 10:00 включить тег", "включить тег", at(2026, 10, 22, 10, 0)),
    ("1 января праздник", "праздник", at(2027, 1, 1, 9, 0)),  # прошло → следующий год
    ("отпуск 3 марта 2027", "отпуск", at(2027, 3, 3, 9, 0)),
    ("отпуск 3 марта 2027 г.", "отпуск", at(2027, 3, 3, 9, 0)),
    ("встреча 15 окт. 12:00", "встреча", at(2026, 10, 15, 12, 0)),
    ("Встреча 9 Мая", "Встреча", at(2027, 5, 9, 9, 0)),
    # Срок до даты
    ("сравнить рекламу за 2 недели, 22 октября 10:00", "сравнить рекламу за 2 недели", at(2026, 10, 22, 10, 0)),
    ("22 октября 10:00 сравнить итоги за 2 недели", "сравнить итоги за 2 недели", at(2026, 10, 22, 10, 0)),
    ("итоги за 3 дня 24.10 12:00", "итоги за 3 дня", at(2026, 10, 24, 12, 0)),
    ("проверить через 2 недели, а лучше 22 октября", "проверить через 2 недели, а лучше",
     at(2026, 10, 22, 9, 0)),
    ("через 3 дня созвон 2026-10-24 10:30", "через 3 дня созвон", at(2026, 10, 24, 10, 30)),
    # «за N … до <даты>»
    ("за 2 дня до 15 октября купить подарок", "купить подарок", at(2026, 10, 13, 9, 0)),
    ("за 2 дня до 15 октября в 10:00 купить подарок", "купить подарок", at(2026, 10, 13, 10, 0)),
    ("за 1 неделю до 24.10 забронировать отель", "забронировать отель", at(2026, 10, 17, 9, 0)),
    ("за 2 часа до 15 октября 10:00 выехать", "выехать", at(2026, 10, 15, 8, 0)),
    # Относительные слова и срок без даты — как раньше
    ("завтра в 10:00 сравнить за 2 недели", "сравнить за 2 недели", at(2026, 9, 26, 10, 0)),
    ("в пятницу итоги за 3 дня", "итоги за 3 дня", at(2026, 10, 2, 9, 0)),
    ("через 2 часа завтра", "завтра", at(2026, 9, 25, 16, 0)),
    # Не месяц
    ("купить 5 марок", "купить 5 марок", None),
    ("3 майских дня", "3 майских дня", None),
    ("2 мартышки", "2 мартышки", None),
    ("за 2 недели", "за 2 недели", None),
])
def test_duration_with_date_russian(text, title, when):
    assert A.parse_when(text, NOW) == (title, when)


@pytest.mark.parametrize("text, title, when", [
    ("compare ads for 2 weeks, then enable the tag Oct 22 10:00", "compare ads for 2 weeks, then enable the tag",
     at(2026, 10, 22, 10, 0)),
    ("Oct 22 10:00 review the last 2 weeks", "review the last 2 weeks", at(2026, 10, 22, 10, 0)),
    ("check in 2 weeks report due Oct 22", "check in 2 weeks report due", at(2026, 10, 22, 9, 0)),
    ("in 3 days call, 2026-10-24 at 10am", "in 3 days call", at(2026, 10, 24, 10, 0)),
    ("2 days before Oct 15 buy a gift", "buy a gift", at(2026, 10, 13, 9, 0)),
    ("1 week before 2026-10-24 book a hotel", "book a hotel", at(2026, 10, 17, 9, 0)),
    ("3 hours before 24 October 12:00 leave", "leave", at(2026, 10, 24, 9, 0)),
    ("tomorrow at 10:00 review the last 2 weeks", "review the last 2 weeks", at(2026, 9, 26, 10, 0)),
    ("in 2 hours check, tomorrow is late", "check, tomorrow is late", at(2026, 9, 25, 16, 0)),
])
def test_duration_with_date_english(text, title, when):
    assert A.parse_when(text, NOW) == (title, when)


@pytest.mark.parametrize("text, title, when", [
    ("sastanak 22. oktobra u 10:00", "sastanak", at(2026, 10, 22, 10, 0)),
    ("platiti račun 15 novembra", "platiti račun", at(2026, 11, 15, 9, 0)),
    ("1. januara proslava", "proslava", at(2027, 1, 1, 9, 0)),
    ("uporediti oglase za 2 nedelje, 22. oktobra 10:00", "uporediti oglase za 2 nedelje", at(2026, 10, 22, 10, 0)),
    ("2 dana pre 15. oktobra kupiti poklon", "kupiti poklon", at(2026, 10, 13, 9, 0)),
    ("састанак 22. октобра у 10:00", "састанак", at(2026, 10, 22, 10, 0)),
    ("odmor 3. mart 2027", "odmor", at(2027, 3, 3, 9, 0)),
])
def test_duration_with_date_serbian(text, title, when):
    assert A.parse_when(text, NOW) == (title, when)


# ── Дата внутри пути или ссылки — не срок (SERBITO-620) ──
REPRO_620 = "SERBITO-443: подать заявку (факты в ~/claude-handoff/2026-10-14/443-outreach.md) 27.01.2027 10:00"


@pytest.mark.parametrize("text, title, when", [
    # Пример из тикета: дата в пути остаётся в заголовке, срок — явная дата в конце
    (REPRO_620, "SERBITO-443: подать заявку (факты в ~/claude-handoff/2026-10-14/443-outreach.md)",
     at(2027, 1, 27, 10, 0)),
    # Ссылка с датой и явная дата рядом
    ("прочитать https://example.com/2026/10/14/post завтра в 10:00",
     "прочитать https://example.com/2026/10/14/post", at(2026, 9, 26, 10, 0)),
    ("read https://example.com/2026/10/14/post tomorrow 10am",
     "read https://example.com/2026/10/14/post", at(2026, 9, 26, 10, 0)),
    ("read https://example.com/2026-10-14/post on 2026-10-24",
     "read https://example.com/2026-10-14/post", at(2026, 10, 24, 9, 0)),
    ("see https://example.com/news/24.10/12:00 24 oct 3pm",
     "see https://example.com/news/24.10/12:00", at(2026, 10, 24, 15, 0)),
    # Путь с датой и явная дата: две даты в одном тексте
    ("проверить ~/logs/2026-10-14/run.txt 24.10 в 12:00", "проверить ~/logs/2026-10-14/run.txt",
     at(2026, 10, 24, 12, 0)),
    ("check /var/log/2026-10-14.log on friday", "check /var/log/2026-10-14.log", at(2026, 10, 2, 9, 0)),
    (r"открыть C:\reports\2026-10-14\a.xlsx в пятницу", r"открыть C:\reports\2026-10-14\a.xlsx",
     at(2026, 10, 2, 9, 0)),
    ("отчёт 2026-10-24 (черновик в docs/2026-10-14/draft.md)", "отчёт (черновик в docs/2026-10-14/draft.md)",
     at(2026, 10, 24, 9, 0)),
])
def test_date_in_path_or_url_is_not_due(text, title, when):
    assert A.parse_when(text, NOW) == (title, when)


@pytest.mark.parametrize("text", [
    "прочитать https://example.com/2026/10/14/post",
    "read https://example.com/2026-10-14/post",
    "файл ~/claude-handoff/2026-10-14/443-outreach.md",
    "see https://example.com/24.10/at/10:00",
    "release build-2026-10-14 notes",
    "версия v1.24.10 вышла",
    "ticket id:2026-10-14 closed",
])
def test_only_path_date_gives_no_due(text):
    assert A.parse_when(text, NOW) == (text, None)


def test_capture_repro_620():
    """Через parse_task — общий разбор приложения, бота и MCP."""
    p = A.parse_task(0, REPRO_620)  # без #проекта база не нужна
    assert p["title"] == "SERBITO-443: подать заявку (факты в ~/claude-handoff/2026-10-14/443-outreach.md)"
    assert datetime.fromtimestamp(p["remind_at"], A.TZ).replace(tzinfo=None) == datetime(2027, 1, 27, 10, 0)
