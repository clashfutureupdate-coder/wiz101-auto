from wiz101_auto.quest import target_matches


def test_target_matches_enemy_names():
    assert target_matches("wardenvissarovich", ["Iron Paw", "Warden Vissarovich"])
    assert not target_matches("wardenvissarovich", ["Iron Paw"])
    assert not target_matches("ab", ["Abby"])
