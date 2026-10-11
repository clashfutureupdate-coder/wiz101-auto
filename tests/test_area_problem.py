from wiz101_auto.upkeep import is_area_problem


def test_area_problem_box_text():
    assert is_area_problem("Sorry young wizard, an issue occurred with that area. Please try again later.")
    assert is_area_problem("SORRY YOUNG WIZARD, AN ISSUE OCCURRED WITH THAT AREA.")
    assert not is_area_problem("Are you sure you want to flee?")
    assert not is_area_problem("")
