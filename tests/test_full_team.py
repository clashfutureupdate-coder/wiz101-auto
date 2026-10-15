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
