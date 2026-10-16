import json


def test_start_early_waits_for_four(tmp_path, monkeypatch):
    from wiz101_auto import teamup

    sizes = tmp_path / "team_sizes.json"
    sizes.write_text(json.dumps({"G14_DM/DM_Z01_CastleDarkmoor@20,279": 4}), encoding="utf-8")
    monkeypatch.setattr(teamup, "TEAM_SIZE_FILE", sizes)
    monkeypatch.setattr(teamup, "TEAM_LIST_FILE", tmp_path / "team_dungeons.json")
    darkmoor = {"dungeon": "G14_DM/DM_Z01_CastleDarkmoor@20,279", "at": 1000.0}
    assert not teamup.early_ok(darkmoor, now=1000.0 + 60)
    assert teamup.early_ok(darkmoor, now=1000.0 + teamup.FULL_TEAM_WAIT)
    assert teamup.early_ok({"dungeon": "Azteca/AZ_Z05_Somewhere", "at": 1000.0}, now=1001.0)


def test_drop_done_quest_dungeon_won_solo(tmp_path, monkeypatch):
    import json

    from wiz101_auto import teamup

    f = tmp_path / "team.json"
    f.write_text(json.dumps({"Mirage/Interiors/MR_Z02_QuietBungalow": "The Silent Sabers"}))
    monkeypatch.setattr(teamup, "TEAM_LIST_FILE", f)
    monkeypatch.setattr(teamup, "beaten_bases", lambda: set())
    assert teamup.drop_team_dungeons(set()) == []
    got = teamup.drop_team_dungeons(set(), done={"The Silent Sabers"})
    assert got == ["Mirage/Interiors/MR_Z02_QuietBungalow"]
