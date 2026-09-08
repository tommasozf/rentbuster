"""Tests for Telegram update handling and offset persistence."""

from __future__ import annotations

from rentbuster.notify.telegram import TelegramNotifier


class _FakeDB:
    url = "postgres://fake"

    def __init__(self):
        self.state = {}
        self.subscribers = []

    def get_state(self, key):
        return self.state.get(key)

    def set_state(self, key, value):
        self.state[key] = value

    def get_telegram_subscribers(self):
        return list(self.subscribers)

    def add_telegram_subscriber(self, chat_id, username, first_name):
        self.subscribers.append(chat_id)
        return True


def _bot(db=None):
    db = db or _FakeDB()
    bot = TelegramNotifier(bot_token="t", password="pw", db=db)
    bot.sent = []
    bot._send = lambda chat_id, text: bot.sent.append((chat_id, text)) or True
    return bot


def _update(update_id, text, chat_id=42):
    return {"update_id": update_id, "message": {"text": text, "chat": {"id": chat_id, "first_name": "J"}}}


def test_offset_is_restored_from_db():
    db = _FakeDB()
    db.state["telegram_last_update_id"] = "17"
    assert _bot(db)._last_update_id == 17


def test_handle_update_persists_offset_and_dispatches():
    bot = _bot()
    bot._handle_update(_update(5, "/help"))
    assert bot.db.state["telegram_last_update_id"] == "5"
    assert bot.sent and "RentBuster commands" in bot.sent[0][1]


def test_start_with_password_subscribes():
    bot = _bot()
    bot._handle_update(_update(6, "/start pw"))
    assert 42 in bot.db.subscribers


def test_unknown_command_is_ignored_but_offset_advances():
    bot = _bot()
    bot._handle_update(_update(7, "hello there"))
    assert bot.sent == []
    assert bot.db.state["telegram_last_update_id"] == "7"
