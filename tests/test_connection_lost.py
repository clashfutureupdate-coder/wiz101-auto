from wiz101_auto.upkeep import is_connection_lost


def test_connection_lost_box():
    assert is_connection_lost("ATTENTION Connection Lost!")
    assert not is_connection_lost("an issue occurred with that area")
    assert not is_connection_lost(None)
