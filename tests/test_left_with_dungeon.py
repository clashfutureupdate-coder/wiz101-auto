from wiz101_auto.questlist import left_with_dungeon_seen


def test_quest_last_seen_inside_is_flagged():
    seen: dict[str, bool] = {}
    lair = "Polaris/Interiors/PL_Z05_PitBossLair"
    left_with_dungeon_seen(seen, ["Charm of the Mine", "Lair of the Pit Boss"], lair)
    left_with_dungeon_seen(seen, ["Charm of the Mine"], "Polaris/PL_Z05_FrigidMine")
    assert seen["Lair of the Pit Boss"] is True  # gone outside: left with the dungeon
    assert seen["Charm of the Mine"] is False


def test_quest_seen_outside_later_is_not_flagged():
    seen: dict[str, bool] = {}
    left_with_dungeon_seen(seen, ["The Commandant's Quarters"], "Polaris/Interiors/PL_Z04_MayorsHouse")
    left_with_dungeon_seen(seen, ["The Commandant's Quarters"], "Polaris/PL_Z04_UrvilleStation")
    assert seen["The Commandant's Quarters"] is False
