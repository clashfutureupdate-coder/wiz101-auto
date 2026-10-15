"""Shared test setup."""

import importlib.util

import pytest

if importlib.util.find_spec("wizwalker") is None:
    # Linux CI: WizWalker is Windows-only, so the tests get a stand-in.
    from _wizwalker_stub import install

    install()


@pytest.fixture(autouse=True)
def _no_logged_hit_rates(monkeypatch, tmp_path):
    # (The crit gamble reads the fight logs' hit rates, state/enemy_stats.json,
    # rewritten at every bot start: a test running then failed. Tests use
    # the cards' own accuracy.)
    from wiz101_auto.combat import lookahead

    monkeypatch.setattr(lookahead, "RATES_FILE", tmp_path / "no_stats.json")
    monkeypatch.setattr(lookahead, "_RATES", [None, {}])


@pytest.fixture(autouse=True)
def _never_the_real_bot(monkeypatch, tmp_path):
    # (No test may find or stop the running bot: one did, through the real
    # stop.request, three times on 2026-10-16, mid-fight.)
    from wiz101_auto import safety, service

    monkeypatch.setattr(service, "bot_processes", lambda: [])
    monkeypatch.setattr(service, "STOP_FILE", tmp_path / "stop.request")
    monkeypatch.setattr(safety, "PAUSED_FILE", tmp_path / "paused.flag")
