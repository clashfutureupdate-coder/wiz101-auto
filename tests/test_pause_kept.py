from wiz101_auto import safety


def test_pause_is_kept_across_restarts(tmp_path, monkeypatch):
    monkeypatch.setattr(safety, "PAUSED_FILE", tmp_path / "paused.flag")
    c = safety.Controller("ctrl+shift+q", "ctrl+shift+p", 0)
    assert not c.paused
    c.set_paused(True)
    assert c.paused and (tmp_path / "paused.flag").exists()
    again = safety.Controller("ctrl+shift+q", "ctrl+shift+p", 0)  # a restart
    assert again.paused
    again.set_paused(False)
    assert not again.paused and not (tmp_path / "paused.flag").exists()
