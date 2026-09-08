"""Tests for the scrape cycle bookkeeping in rentbuster.scheduler."""

from __future__ import annotations

import pytest

import rentbuster.scheduler as scheduler_mod
from rentbuster.config import Settings
from rentbuster.notify import NotifierBundle
from rentbuster.profile import load_profile
from rentbuster.scheduler import RentBuster


class _EmptySource:
    name = "empty"

    async def fetch_listings(self):
        return []

    async def close(self):
        return None


class _FakeDB:
    def __init__(self):
        self.runs = []

    def load_seen_source_ids(self):
        return set()

    def log_scrape_run(self, source, total, new, bustable=0, error=None):
        self.runs.append((source, total, new, bustable, error))


def _make(monkeypatch, tmp_path, db=None):
    monkeypatch.setattr(scheduler_mod, "HEARTBEAT_PATH", str(tmp_path / "last_run"))
    monkeypatch.setattr(scheduler_mod, "build_sources", lambda settings, profile, **kw: [_EmptySource()])
    return RentBuster(
        settings=Settings(_env_file=None),
        profile=load_profile("amsterdam"),
        db=db,
        notifiers=NotifierBundle([]),
    )


def test_heartbeat_written_on_quiet_cycle(monkeypatch, tmp_path):
    rb = _make(monkeypatch, tmp_path)
    rb.check_once()
    assert (tmp_path / "last_run").exists()
    assert int((tmp_path / "last_run").read_text()) > 0


def test_quiet_cycle_is_logged(monkeypatch, tmp_path):
    db = _FakeDB()
    rb = _make(monkeypatch, tmp_path, db=db)
    rb.check_once()
    assert db.runs == [("all", 0, 0, 0, "no listings")]


def test_heartbeat_and_log_on_error(monkeypatch, tmp_path):
    db = _FakeDB()
    rb = _make(monkeypatch, tmp_path, db=db)

    def boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(rb.notifiers, "process_commands", boom)
    with pytest.raises(RuntimeError):
        rb.check_once()
    assert (tmp_path / "last_run").exists()
    assert db.runs[0][4] == "error: kaboom"
