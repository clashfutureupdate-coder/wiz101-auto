from types import SimpleNamespace

from wiz101_auto.quest import boss_room_entered_from

HALL = "Khrysalis/Interiors/KR_Z11_I13_PalFangsMain"
CHAMBER = "Khrysalis/Interiors/KR_Z11_I16_PalFangsChamb03"


def test_boss_room_with_its_sigil_here():
    dungeons = {CHAMBER: SimpleNamespace(outside=HALL)}
    assert boss_room_entered_from(dungeons, CHAMBER, HALL)


def test_boss_room_entered_elsewhere():
    dungeons = {CHAMBER: SimpleNamespace(outside="Khrysalis/KR_Z11_TheHive")}
    assert not boss_room_entered_from(dungeons, CHAMBER, HALL)
    assert not boss_room_entered_from({}, CHAMBER, HALL)
