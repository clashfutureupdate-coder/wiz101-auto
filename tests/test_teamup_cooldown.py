from wiz101_auto.teamup import cooldown_moving, cooldown_seconds, queued_signs


def test_resume_only_is_not_a_queue():
    assert not queued_signs(None, queued_window=False)  # only RESUME on the sigil
    assert queued_signs("Waiting", queued_window=False)
    assert not queued_signs("", queued_window=False)  # a hidden badge window
    assert not queued_signs("Team Up!", queued_window=False)
    assert queued_signs(None, queued_window=True)


def test_stale_cooldown_text_is_not_a_cooldown():
    assert cooldown_moving(25, 22)
    assert not cooldown_moving(25, 25)  # left over from an earlier cooldown: queued
    assert not cooldown_moving(25, None)


def test_cooldown_read_from_sigil_text():
    assert cooldown_seconds("Team Up!  in 01:24") == 84
    assert cooldown_seconds("TEAM UP! IN 05:51") == 351
    assert cooldown_seconds("Team Up! in 1:02:03") == 3723


def test_not_a_cooldown():
    assert cooldown_seconds("Waiting") is None
    assert cooldown_seconds("") is None
    assert cooldown_seconds(None) is None
