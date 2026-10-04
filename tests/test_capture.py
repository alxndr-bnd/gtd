"""capture: умный захват — @контекст, #проект, дата."""
import app as A


def user():
    return A.email_user("alice@example.com")


def test_plain_text_goes_to_inbox():
    it = A.capture(user(), "  просто мысль  ")
    assert (it["title"], it["status"], it["contexts"], it["project_id"], it["source"]) == \
           ("просто мысль", "inbox", [], None, "web")


def test_context_sends_to_next_lowercased():
    it = A.capture(user(), "позвонить маме @Телефон", "telegram")
    assert (it["title"], it["status"], it["contexts"], it["source"]) == ("позвонить маме", "next", ["телефон"], "telegram")


def test_several_contexts_kept_in_order_without_repeats():
    """SERBITO-423: «@a @b» — все контексты, а не только первый; повтор и регистр не плодят дублей."""
    it = A.capture(user(), "оплатить счёт @Телефон завтра @комп @телефон #Дом")
    assert (it["title"], it["status"], it["contexts"], it["project"]) == \
        ("оплатить счёт", "next", ["телефон", "комп"], "Дом")
    assert A.describe(it).endswith("@телефон @комп  #Дом")
    assert A.capture(user(), "письмо a@b.rs @дом")["contexts"] == ["дом"]  # адрес почты — не контекст


def test_norm_contexts():
    assert A.norm_contexts("@Дом, @телефон  дом") == ["дом", "телефон"]
    assert A.norm_contexts(["@a", "b c"]) == ["a", "b", "c"]
    assert A.norm_contexts(None) == A.norm_contexts("") == A.norm_contexts([]) == []
    assert len(A.norm_contexts(" ".join(f"@c{n}" for n in range(30)))) == A.CONTEXTS_MAX
    for bad in (5, {"a": 1}, ["a", 1]):
        try:
            A.norm_contexts(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_project_created_and_reused_case_insensitively():
    uid = user()
    a = A.capture(uid, "отчёт #Клиент_X")
    b = A.capture(uid, "счёт #клиент_x")
    assert a["status"] == b["status"] == "next"
    assert a["project"] == "Клиент X" and a["project_id"] == b["project_id"]
    assert A.row("select count(*) n from projects where user_id=%s", (uid,))["n"] == 1


def test_projects_are_per_user():
    a = A.capture(user(), "x #Дом")
    b = A.capture(A.email_user("bob@example.com"), "y #Дом")
    assert a["project_id"] != b["project_id"]


def test_reminder_parsed_and_title_cleaned():
    it = A.capture(user(), "позвонить в банк завтра в 10:00 @телефон #Финансы")
    assert it["title"] == "позвонить в банк" and it["remind_at"] and it["reminded"] == 0
    assert (it["contexts"], it["project"]) == (["телефон"], "Финансы")


def test_only_tags_keeps_raw_title():
    it = A.capture(user(), "@дом")
    assert it["title"] == "@дом"
