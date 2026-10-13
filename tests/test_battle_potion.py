from wiz101_auto.combat.fighter import drink_instead_of_pass


def test_potion_instead_of_pass_only_when_low():
    assert drink_instead_of_pass(377, 3947, 3.0)
    assert not drink_instead_of_pass(2000, 3947, 3.0)
    assert not drink_instead_of_pass(377, 3947, 0.5)
