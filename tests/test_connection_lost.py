from wiz101_auto.upkeep import is_connection_lost, is_unreachable


def test_connection_lost_box():
    assert is_connection_lost("ATTENTION Connection Lost!")
    assert not is_connection_lost("an issue occurred with that area")
    assert not is_connection_lost(None)


def test_unable_to_connect_box():
    assert is_unreachable("ATTENTION Unable to connect to server")
    assert not is_unreachable("ATTENTION Connection Lost!")
    assert not is_unreachable(None)
