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
    assert a.places.find("Hollywood Creative Films, Bombay/Los Angeles, America") == ["bombay", "los-angeles"]
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


def _ids(tags, k):
    return [e["id"] for e in tags[k]]


def test_tag_film_place_genre_and_era():
    a = atlas.load(LEX)
    t = atlas.tag_film(a, {"title": "Chal Chal Re Naujawan", "year": 1944, "banner": ["Filmistan Limited, Bombay"],
                           "subject": ["Social and Stunt"]}, "bollywood")
    assert t["place"] == [{"id": "bombay", "src": "banner"}]
    assert _ids(t, "genre") == ["film-social", "film-stunt"]
    assert _ids(t, "wave") == ["bw-early-sound"] and t["wave"][0]["by"] == "year"
    # no banner city: the country stands in; no year: no wave
    t = atlas.tag_film(a, {"title": "X", "country": ["India"]}, "bollywood")
    assert t["place"] == [{"id": "ind", "src": "country"}] and t["wave"] == []


def test_tag_film_wave_rules():
    a = atlas.load(LEX)
    parallel = {"title": "Ankur", "year": 1974, "movement": [{"qid": "Q7134948", "label": "parallel cinema"}]}
    assert {"id": "bw-parallel", "by": "wikidata P135"} in atlas.tag_film(a, parallel, "bollywood")["wave"]
    # a curated wave needs the film on its reviewed list
    assert "bw-angry-young-man" not in _ids(atlas.tag_film(a, {"title": "Deewaar", "year": 1975}, "bollywood"), "wave")
    a.wave_films = [{"wave": "bw-angry-young-man", "title": "Deewaar", "year": "1975", "status": "confirmed"},
                    {"wave": "bw-angry-young-man", "title": "Trishul", "year": "1978", "status": "candidate"}]
    a._listed = None
    assert "bw-angry-young-man" in _ids(atlas.tag_film(a, {"title": "Deewaar", "year": 1975}, "bollywood"), "wave")
    assert "bw-angry-young-man" not in _ids(atlas.tag_film(a, {"title": "Trishul", "year": 1978}, "bollywood"), "wave")
    ott = {"title": "Gulabo Sitabo", "year": 2020, "broadcaster": ["Amazon Prime Video"]}
    assert "bw-ott" in _ids(atlas.tag_film(a, ott, "bollywood"), "wave")
    assert "bw-ott" not in _ids(atlas.tag_film(a, {"title": "Pathaan", "year": 2023}, "bollywood"), "wave")


def test_tag_film_hollywood_rules():
    a = atlas.load(LEX)
    t = atlas.tag_film(a, {"title": "Aladdin", "year": 1992}, "hollywood")
    assert "hw-disney-renaissance" in _ids(t, "wave") and "hw-blockbuster" in _ids(t, "wave")
    musical = {"title": "Top Hat", "year": 1935, "wd_genre": [{"qid": "Q842256"}], "released": "1935-08-29"}
    t = atlas.tag_film(a, musical, "hollywood")
    assert _ids(t, "genre") == ["film-musical"]
    assert "hw-musical-golden-age" in _ids(t, "wave") and "hw-pre-code" not in _ids(t, "wave")
    t = atlas.tag_film(a, {**musical, "year": 1933, "released": "1933-03-09"}, "hollywood")
    assert "hw-pre-code" in _ids(t, "wave")
    freed = {"title": "Meet Me in St. Louis", "year": 1944, "prod": ["Arthur Freed"], "dist": ["MGM"]}
    # studio= reads producers and banners only, so a distributor alone does not place it
    assert "hw-freed-unit" not in _ids(atlas.tag_film(a, freed, "hollywood"), "wave")
    freed["banner"] = ["Metro-Goldwyn-Mayer"]
    assert "hw-freed-unit" in _ids(atlas.tag_film(a, freed, "hollywood"), "wave")


def test_tag_register_counts_and_untagged():
    a = atlas.load(LEX)
    snap = {"films": [{"title": "A", "year": 1950, "banner": ["X, Bombay"], "genre": ["Social"]},
                      {"title": "B", "year": 1951}]}
    out = atlas.tag_register(a, snap, "bollywood")
    assert out["counts"]["wave"]["bw-golden-age"] == 2 and out["untagged"] == {"place": 1, "genre": 1}
    assert snap["films"][0]["atlas"]["place"][0]["id"] == "bombay"


def test_studio_gives_place_and_hollywood_genres():
    a = atlas.load(LEX)
    t = atlas.tag_film(a, {"title": "Meet Me in St. Louis", "year": 1944, "banner": ["MGM"],
                           "genre": ["Musical"], "prod": ["Arthur Freed"]}, "hollywood")
    assert t["place"] == [{"id": "culver-city", "src": "studio"}] and [g["id"] for g in t["genre"]] == ["film-musical"]
    assert "hw-freed-unit" in _ids(t, "wave") and "hw-musical-golden-age" in _ids(t, "wave")
    t = atlas.tag_film(a, {"title": "X", "year": 1944, "banner": ["United Artists"], "country": ["United States"],
                           "genre": ["Crime comedy"]}, "hollywood")
    assert t["place"] == [{"id": "usa", "src": "country"}]
    assert [g["id"] for g in t["genre"]] == ["film-crime", "film-comedy"]
