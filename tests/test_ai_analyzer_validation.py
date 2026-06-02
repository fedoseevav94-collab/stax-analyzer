from app.analyzers.ai_analyzer import _message_ts, _validate_problem


def _message(role, text, index, episode=1, ts=None, message_id=None):
    msg = {
        "role": role,
        "text": text,
        "message_index": index,
        "episode_id": episode,
    }
    if ts is not None:
        msg["created_ts"] = ts
    if message_id is not None:
        msg["message_id"] = str(message_id)
    return msg


def _conv(messages):
    return {"conversation_id": "conv-1", "messages": messages}


def _problem(category, employee_quote, employee_index, client_quote="", client_index=None,
             reasoning="Сотрудник противоречит сообщению клиента", severity="средняя"):
    problem = {
        "category": category,
        "employee_quote": employee_quote,
        "employee_message_index": employee_index,
        "client_quote": client_quote,
        "reasoning": reasoning,
        "severity": severity,
        "confidence": 0.95,
    }
    if client_index is not None:
        problem["client_message_index"] = client_index
    return problem


def test_message_ts_prefers_created_ts():
    assert _message_ts({"created_ts": "123", "date": "456"}) == 123


def test_quote_not_in_specified_employee_message_is_rejected():
    conv = _conv([
        _message("client", "У меня жалоба", 1),
        _message("employee", "Уточним", 2),
        _message("employee", "Жалуйтесь куда хотите", 3),
    ])
    problem = _problem(
        "КОНФЛИКТ",
        "Жалуйтесь куда хотите",
        2,
        client_quote="У меня жалоба",
        client_index=1,
        reasoning="Сотрудник усиливает конфликт после жалобы клиента",
        severity="высокая",
    )

    assert _validate_problem(problem, conv) is None


def test_conflict_employee_reply_before_client_complaint_is_rejected():
    conv = _conv([
        _message("employee", "Жалуйтесь куда хотите", 1),
        _message("client", "У меня жалоба", 2),
    ])
    problem = _problem(
        "КОНФЛИКТ",
        "Жалуйтесь куда хотите",
        1,
        client_quote="У меня жалоба",
        client_index=2,
        reasoning="Сотрудник усиливает конфликт после жалобы клиента",
        severity="высокая",
    )

    assert _validate_problem(problem, conv) is None


def test_conflict_across_different_episodes_is_rejected():
    conv = _conv([
        _message("client", "У меня жалоба", 1, episode=1),
        _message("employee", "Жалуйтесь куда хотите", 2, episode=2),
    ])
    problem = _problem(
        "КОНФЛИКТ",
        "Жалуйтесь куда хотите",
        2,
        client_quote="У меня жалоба",
        client_index=1,
        reasoning="Сотрудник усиливает конфликт после жалобы клиента",
        severity="высокая",
    )

    assert _validate_problem(problem, conv) is None


def test_conflict_with_helpful_reply_is_rejected():
    conv = _conv([
        _message("client", "У меня жалоба", 1),
        _message("employee", "Уточним и свяжемся с вами", 2),
    ])
    problem = _problem(
        "КОНФЛИКТ",
        "Уточним и свяжемся с вами",
        2,
        client_quote="У меня жалоба",
        client_index=1,
        reasoning="Сотрудник усиливает конфликт после жалобы клиента",
        severity="высокая",
    )

    assert _validate_problem(problem, conv) is None


def test_conflict_with_neutral_cooperation_refusal_is_rejected():
    conv = _conv([
        _message("client", "А причину не сказали?", 1),
        _message("employee", "Увидели у вас негативный комментарий, в данный момент мы не можем продолжить сотрудничество.", 2),
    ])
    problem = _problem(
        "КОНФЛИКТ",
        "Увидели у вас негативный комментарий, в данный момент мы не можем продолжить сотрудничество.",
        2,
        client_quote="А причину не сказали?",
        client_index=1,
        reasoning="Сотрудник отказывает без объяснения",
        severity="высокая",
    )

    assert _validate_problem(problem, conv) is None


def test_rudeness_about_technical_dismissal_is_rejected():
    conv = _conv([
        _message("client", "Здравствуйте", 1),
        _message("employee", "как выведите - нам напишите, пожалуйста, чтобы мы вас уволили", 2),
    ])
    problem = _problem(
        "ГРУБОСТЬ",
        "как выведите - нам напишите, пожалуйста, чтобы мы вас уволили",
        2,
        client_quote="Здравствуйте",
        client_index=1,
        reasoning="Сотрудник угрожает уволить клиента",
        severity="высокая",
    )

    assert _validate_problem(problem, conv) is None


def test_conflict_about_technical_dismissal_is_rejected():
    conv = _conv([
        _message("client", "Когда выводить деньги?", 1),
        _message("employee", "Напишите после вывода средств, и мы вас уволим", 2),
    ])
    problem = _problem(
        "КОНФЛИКТ",
        "Напишите после вывода средств, и мы вас уволим",
        2,
        client_quote="Когда выводить деньги?",
        client_index=1,
        reasoning="Сотрудник усиливает конфликт угрозой увольнения",
        severity="высокая",
    )

    assert _validate_problem(problem, conv) is None


def test_incompetence_without_client_quote_is_rejected():
    conv = _conv([_message("employee", "Это невозможно", 1)])
    problem = _problem("НЕКОМПЕТЕНТНОСТЬ", "Это невозможно", 1, client_quote="")

    assert _validate_problem(problem, conv) is None


def test_incompetence_with_weak_reasoning_is_rejected():
    conv = _conv([
        _message("client", "Сейчас все 4 колеса меняют", 1),
        _message("employee", "Все колеса вам не заменят", 2),
    ])
    problem = _problem(
        "НЕКОМПЕТЕНТНОСТЬ",
        "Все колеса вам не заменят",
        2,
        client_quote="Сейчас все 4 колеса меняют",
        client_index=1,
        reasoning="Сотрудник ответил неудачно",
    )

    assert _validate_problem(problem, conv) is None


def test_incompetence_with_far_quotes_is_rejected():
    conv = _conv([
        _message("client", "Сейчас все 4 колеса меняют", 1, ts=100),
        _message("employee", "Все колеса вам не заменят", 2, ts=100 + 7 * 60 * 60),
    ])
    problem = _problem(
        "НЕКОМПЕТЕНТНОСТЬ",
        "Все колеса вам не заменят",
        2,
        client_quote="Сейчас все 4 колеса меняют",
        client_index=1,
        reasoning="Цитаты противоречат друг другу внутри диалога",
    )

    assert _validate_problem(problem, conv) is None


def test_incompetence_with_external_client_claim_is_rejected():
    conv = _conv([
        _message("client", "В интернете вроде написано что можно оформить лицензию", 1),
        _message("employee", "Оформить лицензию на ваш авто не получится", 2),
    ])
    problem = _problem(
        "НЕКОМПЕТЕНТНОСТЬ",
        "Оформить лицензию на ваш авто не получится",
        2,
        client_quote="В интернете вроде написано что можно оформить лицензию",
        client_index=1,
        reasoning="Цитаты противоречат друг другу внутри диалога",
    )

    assert _validate_problem(problem, conv) is None


def test_valid_conflict_is_accepted():
    conv = _conv([
        _message("client", "Вы не вернете мне деньги?", 1, message_id=101),
        _message("employee", "вернуть денежные средства мы не можем", 2, message_id=102),
    ])
    problem = _problem(
        "КОНФЛИКТ",
        "вернуть денежные средства мы не можем",
        2,
        client_quote="Вы не вернете мне деньги?",
        client_index=1,
        reasoning="Сотрудник отказывает после жалобы клиента",
        severity="высокая",
    )

    validated = _validate_problem(problem, conv)

    assert validated is not None
    assert validated["category"] == "КОНФЛИКТ"
    assert validated["message_id"] == "102"
