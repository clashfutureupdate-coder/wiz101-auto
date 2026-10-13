from wiz101_auto.deckitems import cards_to_cross


def test_only_active_wand_fire_cards_get_crossed():
    cards = [("Wand-Fire-T4-005", 1), ("Wand-Fire-T4-005", 0), ("Mythblade - Amulet", 0),
             ("Pet - Feint", 0), ("Pet - 7P Basilisk", 1), ("Wand-Fire-T4-005", 0)]
    assert cards_to_cross(cards) == [1, 5]
    assert cards_to_cross([("Pet - Feint", 0)]) == []
