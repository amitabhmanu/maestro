from pathlib import Path

from maestro import atlas

LEX = Path(__file__).resolve().parents[1] / "maestros" / "atlas" / "lexicon"


def test_shared_lexicons_are_sound():
    a = atlas.load(LEX)
    assert atlas.check(a, LEX) == []
    assert len(a.waves) >= 20 and all(w["sources"] for w in a.waves.values())


def test_place_from_banner_and_old_names():
    a = atlas.load(LEX)
    assert a.places.find("Filmistan Limited, Bombay") == ["bombay"]
    assert a.places.find("Gemini Studios, Bombay/Madras") == ["bombay", "madras"]
    assert a.places.find("Pancholi Art Pictures, Lahore (Panjab)") == ["lahore"]
    assert a.places.find("No city here") == []
    assert "ind" in a.places.ancestors("bombay") and "south-asia" in a.places.ancestors("bombay")


def test_genre_lookup_folds_labels():
    a = atlas.load(LEX)
    assert a.genres.lookup("hfgk", "सामाजिक") == "film-social"
    assert a.genres.lookup("hindigeetmala", "ghazals") == "filmi-ghazal"
    assert a.genres.lookup("hindigeetmala", "Filmfare Awards Winner") is None


def test_eras_place_years():
    a = atlas.load(LEX)
    assert a.waves_for("bollywood", 1957) == ["bw-golden-age"]
    assert a.waves_for("hollywood", 1967) == ["hw-new-hollywood"]
    assert a.waves_for("bollywood", None) == []


def test_rule_parsing():
    assert atlas.parse_rule("curated; year + genre=musical") == [[("curated", "")], [("year", ""), ("genre", "musical")]]


def _write(folder: Path, name: str, header: str, rows: list[str]) -> None:
    (folder / f"{name}.tsv").write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")


def test_check_finds_problems(tmp_path):
    _write(tmp_path, "places", "id\tname\tkind\tparent\twikidata\tlat\tlon\taliases\tvalid_from\tvalid_to\troles\tnotes",
           ["world\tWorld\tworld\t\t\t\t\t\t\t\t\t", "india\tIndia\tcountry\tworld\tX1\t22\t83\t\t\t\t\t",
            "bombay\tMumbai\tcity\tnowhere\t\t19\t72\tBombay\t\t\tstudio\t"])
    _write(tmp_path, "genres", "id\tname\tparent\tkind\twikidata\tmaps_from\tnotes",
           ["film\tFilm\t\tfilm\t\t\t", "film-social\tSocial\tfilm\tfilm\t\thfgk:Social\t",
            "film-drama\tDrama\tfilm\tfilm\t\thfgk:Social\t"])
    _write(tmp_path, "waves", "id\tname\tindustry\tkind\tstart\tend\tdefinition\trule\tsources\twikidata\tnotes",
           ["a\tA\tx\tera\t1930\t1950\tdef\tyear\thttps://e.org\t\t", "b\tB\tx\tera\t1950\t1960\tdef\tyear\t\t\t",
            "c\tC\tx\tmovement\t1940\t1930\tdef\tyear + mood=sad\thttps://e.org\t\t"])
    p = atlas.check(atlas.load(tmp_path), tmp_path)
    text = "\n".join(p)
    for needle in ["country ids are ISO", "is not a QID", "parent nowhere", "unknown roles", "maps to both",
                   "overlap", "needs at least one source", "ends before it starts", "unknown rule term 'mood'"]:
        assert needle in text, needle
