from wiz101_auto.quest import untried_doors


def test_untried_doors_skip_tried_and_learned():
    doors = [(0.0, 0.0), (1000.0, 0.0), (2000.0, 0.0), (3000.0, 0.0)]
    tried = [[10, -20]]  # the first door, tried while mapping
    learned = [[[2050, 30], [1900, 0, 4], "Arcanum/Interiors/AR_Z01_FireSchool"]]
    assert untried_doors(doors, tried, learned) == [(1000.0, 0.0), (3000.0, 0.0)]


def test_untried_doors_all_new():
    assert untried_doors([(5.0, 5.0)], [], []) == [(5.0, 5.0)]


def test_quest_finder_never_completed(tmp_path):
    from wiz101_auto.questlist import CompletionTracker

    t = CompletionTracker(tmp_path / "done.txt")
    t.update({"Quest Finder", "A"})
    assert t.update({"A"}) == []
    assert t.update({"A"}) == []
    t.log(["Quest Finder"])
    assert not (tmp_path / "done.txt").exists()
