from wiz101_auto.teamup import cooldown_seconds


def test_cooldown_read_from_sigil_text():
    assert cooldown_seconds("Team Up!  in 01:24") == 84
    assert cooldown_seconds("TEAM UP! IN 05:51") == 351
    assert cooldown_seconds("Team Up! in 1:02:03") == 3723


def test_not_a_cooldown():
    assert cooldown_seconds("Waiting") is None
    assert cooldown_seconds("") is None
    assert cooldown_seconds(None) is None
