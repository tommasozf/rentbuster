"""Tests for the notifier bundle."""

from __future__ import annotations

from rentbuster.notify import NotifierBundle


class _TextNotifier:
    name = "text"

    def __init__(self):
        self.texts = []

    def send_listings(self, listings):
        pass

    def send_text(self, text):
        self.texts.append(text)


class _ListingsOnlyNotifier:
    name = "listings-only"

    def send_listings(self, listings):
        pass


class _BrokenNotifier:
    name = "broken"

    def send_listings(self, listings):
        pass

    def send_text(self, text):
        raise RuntimeError("down")


def test_send_text_fans_out_and_skips_notifiers_without_it():
    a, b = _TextNotifier(), _TextNotifier()
    bundle = NotifierBundle([a, _ListingsOnlyNotifier(), b])
    bundle.send_text("hello")
    assert a.texts == ["hello"]
    assert b.texts == ["hello"]


def test_send_text_survives_a_failing_notifier():
    ok = _TextNotifier()
    NotifierBundle([_BrokenNotifier(), ok]).send_text("hello")
    assert ok.texts == ["hello"]
