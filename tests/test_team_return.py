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


def test_retalk_after_any_turn_in():
    from wiz101_auto.quest import TURN_IN_TALK_SECONDS, retalk_after_turn_in

    talk = ("Zarozinia the DeathSong", "Khrysalis/KR_Z06_CrescentBeach", 100.0)
    assert retalk_after_turn_in({"Old Man of the Desert"}, set(), talk, 140.0)
    assert not retalk_after_turn_in({"Old Man of the Desert"}, {"Next One"}, talk, 140.0)  # given with it
    assert not retalk_after_turn_in(set(), set(), talk, 140.0)  # nothing handed in
    assert not retalk_after_turn_in({"Old Man of the Desert"}, set(), None, 140.0)  # no talk
    assert not retalk_after_turn_in({"Old Man of the Desert"}, set(), talk, 100.0 + TURN_IN_TALK_SECONDS + 1)
    assert not retalk_after_turn_in({"X"}, set(), ("NPC", "", 100.0), 110.0)  # no zone to go back to


def test_left_team_run_lately(tmp_path, monkeypatch):
    import json

    from wiz101_auto import teamup

    f = tmp_path / "team_run.json"
    monkeypatch.setattr(teamup, "TEAM_RUN_FILE", f)
    room = "Khrysalis/Interiors/KR_Z05_I11_Keep_03"
    f.write_text(json.dumps({"zones": [room], "at": 1000.0, "mate_at": 1000.0}))
    assert teamup.left_team_run_lately("Khrysalis/Interiors/KR_Z05_I11_Keep_03", now=1100.0)
    assert not teamup.left_team_run_lately("Khrysalis/Interiors/KR_Z05_I11_Keep_03",
                                           now=1000.0 + teamup.RESUME_AFTER_DEFEAT + 1)
    assert not teamup.left_team_run_lately("Khrysalis/Interiors/KR_Z06_I02_AntLionWarren", now=1100.0)


def test_no_resume_without_a_teammate_seen(tmp_path, monkeypatch):
    import json

    from wiz101_auto import teamup

    f = tmp_path / "team_run.json"
    monkeypatch.setattr(teamup, "TEAM_RUN_FILE", f)
    f.write_text(json.dumps({"zones": ["Khrysalis/Interiors/KR_Z06_I01_BeastControl"], "at": 1000.0}))
    assert not teamup.left_team_run_lately("Khrysalis/Interiors/KR_Z06_I01_BeastControl", now=1100.0)


def test_ruined_alcazar_is_a_known_place():
    from wiz101_auto.quest import objective_zone

    assert objective_zone("Defeat Deep Spawn in Ruined Alcazar (0 of 2)") == "Khrysalis/KR_Z07_RuinedAlcazar"


def test_boss_flee_counts_as_a_loss_not_a_death():
    from wiz101_auto.safety import Controller

    c = Controller("ctrl+shift+q", "ctrl+shift+p", 0)
    c.record_boss_flee("Khrysalis/Interiors/KR_Z07_I07_PhoenixTop")
    assert c.losses == 1 and c.deaths == 0
    assert c.last_death[1] == "Khrysalis/Interiors/KR_Z07_I07_PhoenixTop"


def test_story_rooms_remembered():
    from wiz101_auto.quest import STORY_ROOMS_KEPT, note_story_room

    stag, serpent = "Khrysalis/Interiors/KR_Z08_I04_StagTower", "Khrysalis/Interiors/KR_Z08_I03_SerpentTower"
    rooms = note_story_room([], stag, "Khrysalis")
    rooms = note_story_room(rooms, "Khrysalis/KR_Z08_RadianceReborn", "Khrysalis")  # outdoors: not a room
    rooms = note_story_room(rooms, serpent, "Khrysalis")
    rooms = note_story_room(rooms, stag, "Khrysalis")  # again: latest last
    assert rooms == [serpent, stag]
    assert note_story_room(rooms, "Aquila/Interiors/X", "Khrysalis") == rooms  # another world
    many = rooms
    for i in range(10):
        many = note_story_room(many, f"Khrysalis/Interiors/R{i}", "Khrysalis")
    assert len(many) == STORY_ROOMS_KEPT


def test_quest_props_are_not_people():
    from wiz101_auto.givers import looks_like_person

    for prop in ("Sea Salt", "Burrow Shovel", "Mining Equipment", "Obelisk Orb"):
        assert not looks_like_person(prop)
    assert looks_like_person("Zarozinia the DeathSong")


def test_handed_in_quest_logged_at_once(tmp_path):
    from wiz101_auto.questlist import CompletionTracker, load_completed

    log = tmp_path / "done.txt"
    t = CompletionTracker(log)
    t.update({"Cor Serpentis", "Into the Sea"})
    assert t.confirm({"Cor Serpentis"}) == ["Cor Serpentis"]
    assert load_completed(log) == ["Cor Serpentis"]
    assert t.confirm({"Cor Serpentis"}) == []  # (once)
    assert t.update({"Into the Sea"}) == []  # (no second logging from the two-reads rule)
    assert t.update({"Into the Sea"}) == []


def test_lost_cards_come_back():
    from wiz101_auto.deck_keeper import restore_counts

    base = {"Gargantuan": 2, "Humongofrog": 1, "Mythblade": 2, "Orthrus - T02 - A": 2, "Feint": 1,
            "Spirit Blade": 1}
    got = restore_counts({"Gargantuan": 1, "Feint": 3}, base)
    assert got == base  # (3 trained Feints back down to the player's 1)


def test_colossus_piece_is_a_statue_piece():
    from wiz101_auto.collect import loose_names, matches_item

    names = loose_names("Mystic Colossus Piece")
    assert any(matches_item(n, "KR_StatuePiece03") for n in names)
