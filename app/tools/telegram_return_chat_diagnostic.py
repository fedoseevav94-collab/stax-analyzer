"""
Manual diagnostic for the Telegram return-tasks chat.

It checks whether the configured bot can see the chat and pending updates from it.
The script never prints the bot token.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests


TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
DEFAULT_RETURN_TASKS_CHAT_ID = "-1002393474582"
MoscowTZ = ZoneInfo("Europe/Moscow")


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def _preview(value, limit: int = 180) -> str:
    text = _clean(value)
    if len(text) > limit:
        return text[:limit - 1].rstrip() + "…"
    return text


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"WARNING: {name}={raw!r} is not a number, using {default}")
        return default


def _call(token: str, method: str, payload: dict | None = None):
    response = requests.post(
        TELEGRAM_API.format(token=token, method=method),
        json=payload or {},
        timeout=20,
    )
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{method}: Telegram вернул не JSON: HTTP {response.status_code}") from exc
    if not data.get("ok"):
        description = _preview(data.get("description"), 500)
        raise RuntimeError(f"{method}: {description or 'Telegram API error'}")
    return data.get("result")


def _try_call(token: str, method: str, payload: dict | None = None):
    try:
        return _call(token, method, payload), None
    except Exception as exc:
        return None, str(exc)


def _message_from_update(update: dict) -> dict | None:
    return update.get("message") or update.get("channel_post")


def _message_text(message: dict) -> str:
    return _clean(message.get("text")) or _clean(message.get("caption"))


def _message_datetime(message: dict) -> str:
    timestamp = message.get("date")
    if not timestamp:
        return ""
    return datetime.fromtimestamp(int(timestamp), MoscowTZ).strftime("%d.%m.%Y %H:%M:%S МСК")


def _sender_label(message: dict) -> str:
    sender = message.get("from") or message.get("sender_chat") or {}
    username = _clean(sender.get("username"))
    name = _clean(" ".join(filter(None, [sender.get("first_name"), sender.get("last_name")])))
    title = _clean(sender.get("title"))
    label = username or name or title or "unknown"
    if sender.get("is_bot"):
        label += " (bot)"
    return label


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("RETURN_TASKS_CHAT_ID", DEFAULT_RETURN_TASKS_CHAT_ID).strip()
    limit = _int_env("TELEGRAM_UPDATES_LIMIT", 100)
    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN is missing")
        return 2

    print("Telegram return-tasks chat diagnostic")
    print(f"Target chat id: {chat_id}")

    me = _call(token, "getMe")
    bot_id_value = me.get("id")
    bot_id = str(bot_id_value or "")
    bot_username = _clean(me.get("username"))
    print(f"Bot: @{bot_username or 'unknown'} / id={bot_id}")

    webhook, webhook_error = _try_call(token, "getWebhookInfo")
    if webhook_error:
        print(f"Webhook info: ERROR: {webhook_error}")
    else:
        has_webhook = bool(_clean(webhook.get("url")))
        print(f"Webhook configured: {'yes' if has_webhook else 'no'}")
        print(f"Pending updates: {webhook.get('pending_update_count', 0)}")
        if has_webhook:
            print("NOTE: getUpdates обычно недоступен, пока у бота включён webhook.")

    chat, chat_error = _try_call(token, "getChat", {"chat_id": chat_id})
    if chat_error:
        print(f"Chat visible: no ({chat_error})")
    else:
        title = _clean(chat.get("title")) or _clean(chat.get("username")) or "without title"
        print(f"Chat visible: yes ({title})")

    member, member_error = _try_call(token, "getChatMember", {"chat_id": chat_id, "user_id": bot_id_value})
    if member_error:
        print(f"Bot membership: unknown ({member_error})")
    else:
        print(f"Bot membership: {member.get('status', 'unknown')}")

    updates, updates_error = _try_call(
        token,
        "getUpdates",
        {
            "limit": max(1, min(limit, 100)),
            "timeout": 1,
            "allowed_updates": ["message", "channel_post", "my_chat_member"],
        },
    )
    if updates_error:
        print(f"Updates readable: no ({updates_error})")
        print("Что сделать: если включён webhook, проверять надо через webhook-лог или временно отдельным ботом.")
        return 0

    print(f"Updates readable: yes ({len(updates)} pending updates checked)")
    matching_messages = []
    for update in updates:
        message = _message_from_update(update)
        if not message:
            continue
        chat = message.get("chat") or {}
        if str(chat.get("id")) == chat_id:
            matching_messages.append(message)

    bot_authored = [
        message for message in matching_messages
        if (message.get("from") or {}).get("is_bot")
    ]
    return_task_messages = [
        message for message in matching_messages
        if "запись на возврат авто" in _message_text(message).lower()
    ]

    print(f"Messages from target chat: {len(matching_messages)}")
    print(f"Bot-authored messages visible: {'yes' if bot_authored else 'no'}")
    print(f"Return-task cards visible: {len(return_task_messages)}")

    if matching_messages:
        print("")
        print("Last matching messages:")
        for message in matching_messages[-5:]:
            print(
                f"- #{message.get('message_id')} "
                f"{_message_datetime(message)} "
                f"from={_sender_label(message)} "
                f"text={_preview(_message_text(message), 220)!r}"
            )
    else:
        print("")
        print("Новых сообщений из целевого чата среди pending updates нет.")
        print("Отправьте в чат новое тестовое сообщение от человека и дождитесь новой карточки от STAX Бота, затем запустите workflow ещё раз.")

    if matching_messages and not bot_authored:
        print("")
        print("Важно: бот видит чат, но пока не видно сообщений от других ботов.")
        print("Если карточки сдач пишет STAX Бот, может понадобиться Bot-to-Bot Communication Mode.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
