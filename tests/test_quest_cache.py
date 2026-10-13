from wiz101_auto.quest import RANK_FULL_EVERY, QuestEntry, quest_cache_data, quests_from_cache


def _cache(now=1000.0, pin=""):
    quests = [
        QuestEntry(slot=0, name="Storming the Basstille", world="The Basstille", zone="Polaris",
                   mainline=True, goal="Talk To Old Man", target="Old Man", active=True, hops=2),
        QuestEntry(slot=1, name="Quest Finder"),
        QuestEntry(slot=2, name="Into the Sea", world="Atlantea", zone="Aquila", mainline=True),
    ]
    memory = {"Storming the Basstille", "Quest Finder", "Into the Sea", "A Foul Decree"}
    return quest_cache_data(quests, memory, pin, now)


def test_cache_keeps_ranking_fields_and_memory_extras():
    data = _cache()
    assert data["extras"] == ["afouldecree"]
    fields = data["quests"]["Storming the Basstille"]
    assert fields["mainline"] and fields["world"] == "The Basstille"
    assert "slot" not in fields and "active" not in fields and "hops" not in fields


def test_hit_builds_entries_with_the_tracked_quest_live():
    names = {"Storming the Basstille ", "Quest Finder", "Into the Sea", "A Foul Decree"}
    got = quests_from_cache(_cache(), names, "Into the Sea", "Defeat Kraken in Atlantea (0 of 2)", "", 1100.0)
    assert [q.name for q in got] == ["Storming the Basstille", "Quest Finder", "Into the Sea"]
    sea = got[2]
    assert sea.active
    assert sea.goal == "Defeat Kraken in Atlantea (0 of 2)" and sea.target == "Kraken"
    assert sea.fight and sea.counted and sea.mainline
    assert not got[0].active and got[0].hops is None


def test_a_quest_gone_from_memory_is_left_out():
    names = {"Storming the Basstille", "Into the Sea"}
    got = quests_from_cache(_cache(), names, "Into the Sea", "", "", 1100.0)
    assert [q.name for q in got] == ["Storming the Basstille", "Into the Sea"]


def test_misses():
    names = {"Storming the Basstille", "Quest Finder", "Into the Sea"}
    assert quests_from_cache(_cache(), names | {"A New Quest"}, "", "", "", 1100.0) is None  # new quest
    assert quests_from_cache(_cache(), names, "", "", "", 1000.0 + RANK_FULL_EVERY + 1) is None  # too old
    assert quests_from_cache(_cache(), names, "", "", "Quest Finder", 1100.0) is None  # pin changed
    assert quests_from_cache(None, names, "", "", "", 1100.0) is None
    assert quests_from_cache(_cache(), set(), "", "", "", 1100.0) is None  # memory read nothing
    assert quests_from_cache(_cache(), {"Quest Finder"}, "", "", "", 1100.0) is None  # most "done" at once
