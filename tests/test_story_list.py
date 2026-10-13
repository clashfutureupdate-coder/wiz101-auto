from wiz101_auto.quest import on_story_list
from wiz101_auto.questlist import ListedQuest


def test_story_list_match():
    lists = {"Polaris": [ListedQuest(11, "Sea Chicken", "Walruskberg Harbor", ["INSTANCE"])]}
    assert on_story_list("Sea Chicken", lists)
    assert not on_story_list("Let's Duet!", lists)
    assert not on_story_list("", lists)
