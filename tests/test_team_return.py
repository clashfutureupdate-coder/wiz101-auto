from wiz101_auto.marks import TEAM_RETURN_BUDGET, TEAM_RETURN_CAP, team_return_action
from wiz101_auto.potions import MIN_POTIONS, should_buy_potions


def test_team_return_heals_then_recalls_within_the_budget():
    assert team_return_action(0.7, 0, 5) == "recall"  # healthy enough: straight back
    assert team_return_action(0.02, 2, 5) == "potion"  # potions in hand first
    assert team_return_action(0.02, 0, 5) == "heal"  # this zone's wisps
    assert team_return_action(0.3, 0, TEAM_RETURN_BUDGET + 1) == "recall"  # the budget's up
    assert team_return_action(0.1, 0, TEAM_RETURN_BUDGET + 1) == "heal"  # too low yet
    assert team_return_action(0.1, 0, TEAM_RETURN_CAP + 1) == "recall"  # never longer than the cap


def test_potions_bought_below_three_never_with_a_team():
    assert MIN_POTIONS == 3
    assert should_buy_potions(0, 4)
    assert should_buy_potions(2.5, 4)  # (partial charges count as less)
    assert not should_buy_potions(3, 4)
    assert should_buy_potions(1, 2)  # holds 2 at most: below that
    assert not should_buy_potions(2, 2)
    assert not should_buy_potions(0, 0)  # no potion slots yet
    assert not should_buy_potions(0, 4, in_team_dungeon=True)  # the team moves on without us
    assert not should_buy_potions(0, 4, team_return=True)  # back to the team first
