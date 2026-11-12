import copy

import pytest
import yaml

from conftest import PILOTS, ROOT
from maestro.cli import validate_main
from maestro.pack import PackError, load_pack, validate


def raw(slug="rafi"):
    return yaml.safe_load((ROOT / "maestros" / slug / "maestro.yaml").read_text("utf-8"))


@pytest.mark.parametrize("slug", PILOTS)
def test_pilot_packs_are_valid(slug):
    pack = load_pack(ROOT / "maestros" / slug)
    assert pack.slug == slug


def test_pilots_cover_different_canon_models():
    units = {load_pack(ROOT / "maestros" / s).unit for s in PILOTS}
    assert units == {"song", "recording", "work"}


def test_unknown_unit_is_refused():
    data = raw()
    data["canon_model"]["unit"] = "ringtone"
    assert any("canon_model/unit" in p for p in validate(data))


def test_primary_role_must_be_in_scope():
    data = raw()
    data["roles"]["in_scope"] = ["lyricist"]
    assert validate(data) == ["roles.primary must be listed in roles.in_scope"]


def test_voice_verification_needs_a_singer():
    data = raw("jean-wiener")
    data["verification"]["method"] = "voice"
    assert any("voice" in p for p in validate(data))


def test_register_tiers_must_exist():
    data = raw()
    data["registers"][0]["tiers"] = ["Z"]
    assert any("unknown tiers" in p for p in validate(data))


def test_full_lyrics_only_for_personal_use():
    data = raw()
    data["legal"]["lyrics"] = "private_full"
    assert any("private_full" in p for p in validate(data))
    data["legal"]["purpose"] = "personal"
    assert validate(data) == []


def test_slug_must_match_folder(tmp_path):
    data = copy.deepcopy(raw())
    folder = tmp_path / "someone-else"
    folder.mkdir()
    (folder / "maestro.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")
    with pytest.raises(PackError, match="must match folder name"):
        load_pack(folder)


def test_validate_cli(capsys):
    assert validate_main([str(ROOT / "maestros" / s) for s in PILOTS]) == 0
    assert capsys.readouterr().out.count("ok ") == 3
