import json
import time

import pytest

from wiz101_auto import quadbox, quadlink
from wiz101_auto.quadbox import Game, adopt_window, assign_roles, check_names, match_questers
from wiz101_auto.quadlink import FriendTeleports, Mate, booster_plan, fight_to_join, mate_inside


class P:
    def __init__(self, x, y, z=0.0):
        self.x, self.y, self.z = x, y, z


# --- which game is which wizard ---

def _games():
    # Opened in this order: 11, 12, 13, 14 (14 is the booster, level 60).
    return [Game(11, 100.0, 20, "Myth", 1500), Game(12, 200.0, 21, "Fire", 1400),
            Game(13, 300.0, 19, "Ice", 1900), Game(14, 50.0, 60, "Life", 5000)]


def test_first_time_questers_go_by_opening_order_and_the_highest_level_boosts():
    roles, how = assign_roles(_games(), {})
    assert roles == {"q1": 11, "q2": 12, "q3": 13, "booster": 14}
    assert "order" in how


def test_remembered_questers_keep_their_roles_whatever_the_opening_order():
    memory = {"q1": {"school": "Ice", "level": 19, "max_hp": 1900},
              "q2": {"school": "Myth", "level": 20, "max_hp": 1500},
              "q3": {"school": "Fire", "level": 20, "max_hp": 1350}}
    roles, how = assign_roles(_games(), memory)
    assert roles == {"q1": 13, "q2": 11, "q3": 12, "booster": 14}
    assert how == "remembered"


def test_a_level_lower_than_remembered_isnt_that_wizard():
    memory = {"q1": {"school": "Myth", "level": 25, "max_hp": 1500},
              "q2": {"school": "Fire", "level": 21, "max_hp": 1400},
              "q3": {"school": "Ice", "level": 19, "max_hp": 1900}}
    assert match_questers(_games()[:3], memory) is None


def test_two_alike_questers_fall_back_to_the_opening_order():
    games = [Game(1, 3.0, 20, "Myth", 1500), Game(2, 1.0, 20, "Myth", 1500), Game(3, 2.0, 30, "Fire", 2000),
             Game(4, 0.0, 90, "Life", 6000)]
    memory = {"q1": {"school": "Myth", "level": 20, "max_hp": 1500},
              "q2": {"school": "Myth", "level": 20, "max_hp": 1500},
              "q3": {"school": "Fire", "level": 30, "max_hp": 2000}}
    roles, how = assign_roles(games, memory)
    assert roles == {"q1": 2, "q2": 3, "q3": 1, "booster": 4}  # (opening order: 2, 3, 1)
    assert "order" in how


def test_the_booster_must_be_the_highest_level():
    games = _games()
    games[0].level = 60
    with pytest.raises(ValueError, match="highest level"):
        assign_roles(games, {})
    # ... unless the booster is remembered and only one of the two fits it.
    roles, _ = assign_roles(games, {"booster": {"school": "Life", "level": 60, "max_hp": 5000}})
    assert roles["booster"] == 14


def test_four_games_are_needed():
    with pytest.raises(ValueError, match="exactly 4"):
        assign_roles(_games()[:3], {})


def test_a_reopened_game_goes_to_the_wizard_whose_game_closed():
    roles = {"q1": 11, "q2": 12, "q3": 13, "booster": 14}
    alive = {11, 13, 14, 99}
    windows = {11: 0.0, 13: 0.0, 14: 0.0, 99: 1000.0}
    assert adopt_window(roles, alive, windows, now=1000.0 + quadbox.ADOPT_AFTER + 1) == ("q2", 99)
    assert adopt_window(roles, alive, windows, now=1010.0) is None  # (too new: a restart claims it itself)
    # Two closed, or two new: not clear which is whose.
    assert adopt_window(roles, {13, 14, 99}, {13: 0, 14: 0, 99: 0}, now=1e6) is None
    assert adopt_window(roles, {11, 13, 14, 98, 99}, {11: 0, 13: 0, 14: 0, 98: 0, 99: 0}, now=1e6) is None


def test_names_must_be_filled_in():
    assert "q2, q3" in check_names({"q1": "Ann Storm", "q2": "Name Two"})
    assert check_names({"q1": "Ann Storm", "q2": "Bo Fire", "q3": "Cy Ice"}) == ""


# --- what the booster and questers do ---

def _mate(role, zone="WC/Streets", pos=(0.0, 0.0, 0.0), **kw):
    return Mate(role=role, zone=zone, zone_since=kw.pop("zone_since", 0.0), pos=pos, time=time.time(), **kw)


def test_booster_joins_the_nearest_fight_in_its_own_zone_first():
    mates = {"q1": _mate("q1", zone="A", in_battle=True),
             "q2": _mate("q2", zone="B", pos=(500.0, 0.0, 0.0), in_battle=True),
             "q3": _mate("q3", zone="B", pos=(100.0, 0.0, 0.0), in_battle=True)}
    assert booster_plan(mates, "B", (0.0, 0.0, 0.0)) == ("fight", "q3")
    assert booster_plan(mates, "C", (0.0, 0.0, 0.0)) == ("fight", "q1")  # (none here: the lead's)


def test_booster_follows_the_lead_once_it_stays_in_a_zone():
    now = 1000.0
    mates = {"q1": _mate("q1", zone="A", zone_since=now - 2), "q2": _mate("q2", zone="B")}
    assert booster_plan(mates, "B", (0.0, 0.0, 0.0), now=now) is None  # (q1 only passing through A)
    mates["q1"].zone_since = now - 60
    assert booster_plan(mates, "B", (0.0, 0.0, 0.0), now=now) == ("zone", "q1")
    assert booster_plan(mates, "A", (5000.0, 0.0, 0.0), now=now) == ("near", "q1")
    assert booster_plan(mates, "A", (100.0, 0.0, 0.0), now=now) is None


def test_booster_skips_paused_and_loading_questers():
    mates = {"q1": _mate("q1", zone="A", state="paused"), "q2": _mate("q2", zone="B", loading=True),
             "q3": _mate("q3", zone="C")}
    assert booster_plan(mates, "Z", (0.0, 0.0, 0.0), now=1e9) == ("zone", "q3")
    assert booster_plan({}, "Z", (0.0, 0.0, 0.0)) is None


def test_quester_joins_a_teammates_fight_on_the_same_objective_nearby():
    mates = {"q2": _mate("q2", zone="A", pos=(800.0, 0.0, 0.0), in_battle=True,
                         objective="Defeat Rattlebones"),
             "booster": _mate("booster", zone="A", in_battle=True, objective="Defeat Rattlebones")}
    assert fight_to_join(mates, "A", (0.0, 0.0, 0.0), "Defeat Rattlebones") == "q2"
    assert fight_to_join(mates, "A", (0.0, 0.0, 0.0), "Talk to Gamma") is None
    assert fight_to_join(mates, "A", (0.0, 0.0, 0.0), "Defeat Lord Nightshade") is None
    assert fight_to_join(mates, "B", (0.0, 0.0, 0.0), "Defeat Rattlebones") is None
    assert fight_to_join(mates, "A", (9000.0, 0.0, 0.0), "Defeat Rattlebones") is None


def test_a_teammate_inside_the_dungeon_of_this_sigil():
    mates = {"q1": _mate("q1", zone="WC/Dungeon_Room01", entered=("WC/Streets", 100.0, 100.0)),
             "q3": _mate("q3", zone="WC/Other", entered=("WC/Elsewhere", 100.0, 100.0))}
    assert mate_inside(mates, "WC/Streets", P(150.0, 120.0)) == "q1"
    assert mate_inside(mates, "WC/Streets", P(5000.0, 0.0)) is None  # (another sigil)
    mates["q1"].zone = "WC/Streets"  # (back out)
    assert mate_inside(mates, "WC/Streets", P(150.0, 120.0)) is None


def test_posts_round_trip_and_go_stale(tmp_path):
    quadlink.publish({"zone": "A", "pos": (1, 2, 3), "in_battle": True, "entered": ("O", 1.0, 2.0)},
                     folder=tmp_path, me="q1")
    mates = quadlink.read_mates(folder=tmp_path, me="q2")
    assert mates["q1"].zone == "A" and mates["q1"].in_battle and mates["q1"].entered == ("O", 1.0, 2.0)
    assert "q1" not in quadlink.read_mates(folder=tmp_path, me="q1")  # (not ourselves)
    data = json.loads((tmp_path / "q1.json").read_text())
    assert quadlink.read_mates(folder=tmp_path, me="q2", now=data["time"] + quadlink.STALE + 1) == {}


def test_friend_teleports_back_off_after_failures():
    tp = FriendTeleports()
    assert tp.may_try("q1", now=0.0)
    tp.note("q1", False, now=0.0)
    assert not tp.may_try("q1", now=10.0) and tp.may_try("q1", now=quadlink.FRIEND_TP_GAP)
    for t in (100.0, 200.0):
        tp.note("q1", False, now=t)
    assert not tp.may_try("q1", now=200.0 + quadlink.FRIEND_TP_GAP)
    assert tp.may_try("q1", now=200.0 + quadlink.FRIEND_TP_BACKOFF)
    tp.note("q1", True, now=1000.0)
    assert tp.may_try("q1", now=1000.0 + quadlink.FRIEND_TP_GAP)


# --- quadbox mode switches ---

def test_roles_come_from_the_environment(monkeypatch):
    monkeypatch.delenv("WIZ101_ROLE", raising=False)
    assert not quadlink.active()
    monkeypatch.setenv("WIZ101_ROLE", "booster")
    assert quadlink.active() and quadlink.is_booster()
    monkeypatch.setenv("WIZ101_ROLE", "q9")
    assert not quadlink.active()


def test_no_team_up_in_quadbox(monkeypatch, tmp_path):
    from wiz101_auto import teamup

    listed = tmp_path / "team_dungeons.json"
    listed.write_text(json.dumps({"MooShu/MS_Dungeon": "A Quest"}))
    monkeypatch.setattr(teamup, "TEAM_LIST_FILE", listed)
    monkeypatch.delenv("WIZ101_ROLE", raising=False)
    assert teamup.is_team_dungeon("MooShu/MS_Dungeon")
    monkeypatch.setenv("WIZ101_ROLE", "q1")
    assert teamup.team_list() == {}
    assert not teamup.is_team_dungeon("MooShu/MS_Dungeon")
    assert not teamup.is_team_up_zone("MooShu/MS_Dungeon")


def test_each_wizard_has_its_own_login(monkeypatch):
    from wiz101_auto.gamerestart import CRED_TARGET, cred_target

    monkeypatch.delenv("WIZ101_ROLE", raising=False)
    assert cred_target() == CRED_TARGET
    assert cred_target("q2") == CRED_TARGET + "/q2"
    monkeypatch.setenv("WIZ101_ROLE", "booster")
    assert cred_target() == CRED_TARGET + "/booster"


def test_one_game_restart_at_a_time(monkeypatch, tmp_path):
    from wiz101_auto import gamerestart

    monkeypatch.setenv("WIZ101_ROLE", "q1")
    monkeypatch.setenv("WIZ101_SHARED", str(tmp_path))
    lock = tmp_path / "game_restart.lock"
    with gamerestart.restart_lock():
        assert lock.exists()
    assert not lock.exists()
    lock.write_text("1 0")  # (left by a restart that died long ago)
    import os

    os.utime(lock, (0, 0))
    with gamerestart.restart_lock():
        pass
    assert not lock.exists()


def test_quadbox_config(tmp_path):
    from wiz101_auto.config import load_config

    cfg = load_config("configs/quadbox.yaml")
    assert cfg.quadbox.names == {"q1": "Name One", "q2": "Name Two", "q3": "Name Three"}
    assert cfg.combat.strategy.heal_threshold == 0.5 and not cfg.gear_checks
    assert not cfg.quest.accept_side_quests and cfg.progression.school == ""
    bad = tmp_path / "bad.yaml"
    bad.write_text('quadbox:\n  names: {q4: "X"}\n')
    with pytest.raises(ValueError, match="q4"):
        load_config(bad)
    boost = tmp_path / "boost.yaml"
    boost.write_text("mode: boost\n")
    assert load_config(boost).mode == "boost"


# --- v2: the quest window, reagents, main story only ---

def test_quest_window_rows(monkeypatch, tmp_path):
    from wiz101_auto import quadpanel

    monkeypatch.setattr(quadbox, "QUAD", tmp_path)
    monkeypatch.setattr(quadpanel, "bot_pid", lambda r: 1 if r != "q3" else 0)
    now = 1_000_000.0
    st = tmp_path / "q1" / "state"
    st.mkdir(parents=True)
    (st / "status.json").write_text(json.dumps({
        "time": now - 3, "state": "running", "in_battle": False, "level": 22, "health": "900/1200",
        "zone": "WizardCity/WC_Streets/WC_Unicorn", "quest": "Peace in the Streets",
        "objective": "Defeat Rattlebones", "objective_age_s": 125, "objectives_completed": 7, "deaths": 1}))
    bst = tmp_path / "booster" / "state"
    bst.mkdir(parents=True)
    (bst / "status.json").write_text(json.dumps({"time": now - 2, "state": "running", "in_battle": True}))
    rows = {r["role"]: r for r in quadpanel.panel_rows({"q1": "Ann Storm"}, now=now)}
    assert rows["q1"]["name"] == "Ann Storm" and rows["q1"]["state"] == "running"
    assert rows["q1"]["quest"] == "Peace in the Streets"
    assert rows["q1"]["objective"] == "Defeat Rattlebones   (2m on it)"
    assert rows["q1"]["zone"] == "WC_Unicorn" and rows["q1"]["done"] == "7"
    assert rows["q2"]["state"] == "starting"  # (running, no status yet)
    assert rows["q3"]["state"] == "stopped"
    assert rows["booster"]["state"] == "fighting" and "booster" in rows["booster"]["quest"]


def test_reagents_off_chests_still_on():
    from wiz101_auto.collect import is_collectable

    assert is_collectable("Parchment") and not is_collectable("Parchment", reagents=False)
    assert is_collectable("WC-Chest-Common-001", reagents=False)
    assert is_collectable("COLLECT_Gnome", reagents=False)


def test_main_story_only_and_no_reagents_by_default():
    from wiz101_auto.config import Config, load_config

    assert not Config().quest.accept_side_quests and not Config().quest.collect_reagents
    for preset in ("configs/quadbox.yaml", "configs/myth.yaml"):
        cfg = load_config(preset)
        assert not cfg.quest.accept_side_quests and not cfg.quest.collect_reagents, preset
