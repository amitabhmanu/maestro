"""Core extractors for work-level canons (Wikidata films, MusicBrainz works, BnF works) and title
matching when no source places works in containers."""

from maestro.extractors import bnf_works, musicbrainz_works, wikidata_films
from maestro.resolve import Groups, open_reviews, resolve

ME = "me"


def test_musicbrainz_works_skip_parts_and_uncredited():
    data = {"work-count": 3, "work-offset": 0, "works": [
        {"id": "w1", "title": "Sonatine n° 2", "type": "Sonata",
         "relations": [{"type": "composer", "artist": {"id": ME}, "begin": "1928"}]},
        {"id": "w2", "title": "Sonatine n° 2: I. Agréable", "type": None,
         "relations": [{"type": "composer", "artist": {"id": ME}},
                       {"type": "parts", "direction": "backward", "work": {"id": "w1"}}]},
        {"id": "w3", "title": "Je te veux", "type": None, "relations": [{"type": "composer", "artist": {"id": "satie"}}]},
    ]}
    page = musicbrainz_works.parse(data, ME)
    assert [(w["id"], w["roles"], w["year"]) for w in page["works"]] == [("w1", ["composer"], 1928)]
    assert page["page_size"] == 3


def test_wikidata_films_take_the_original_title_and_first_year():
    data = {"results": {"bindings": [
        {"film": {"value": "http://www.wikidata.org/entity/Q1"}, "title": {"value": "Touchez pas au grisbi"},
         "en": {"value": "Grisbi"}, "year": {"value": "1954"}},
        {"film": {"value": "http://www.wikidata.org/entity/Q2"}, "label": {"value": "La Bandera"}},
    ]}}
    assert wikidata_films.parse(data) == [{"qid": "Q1", "title": "Touchez pas au grisbi", "year": 1954},
                                          {"qid": "Q2", "title": "La Bandera", "year": None}]
    assert "wd%3AQ553384" in wikidata_films.films_url("Q553384")


def test_bnf_works_split_the_subtitle_and_mark_film_scores():
    data = {"results": {"bindings": [
        {"work": {"value": "http://data.bnf.fr/ark:/12148/cb181120097#about"},
         "title": {"value": "Le capitan : Musique de film"}, "date": {"value": "1947"}},
        {"work": {"value": "http://data.bnf.fr/temp-work/636c/#about"}, "title": {"value": "Allegro appassionato"}},
    ]}}
    works = bnf_works.parse(data)
    assert [(w["ark"], w["title"], w["film"], w["year"]) for w in works] == [
        ("636c", "Allegro appassionato", False, None), ("cb181120097", "Le capitan", True, 1947)]


def test_works_without_containers_match_by_title(store_for):
    s = store_for("jean-wiener")
    me = s.maestro_entity()

    def work(source, xid, title, year=None, kind=None):
        u = s.find_or_create_by_xref("work", source, xid, title)
        s.upsert_claim(u, "title", value=title, source_id=source)
        s.upsert_claim(u, "credit", ref_entity_id=me, value={"role": "composer"}, source_id=source)
        if year:
            s.upsert_claim(u, "year", value=year, source_id=source)
        if kind:
            s.upsert_claim(u, "work_kind", value=kind, source_id=source)
        return u

    film = work("wikidata", "film:Q1", "La Bandera", 1935, "film_score")
    bnf = work("bnf", "work:1", "La bandera", 1935)
    sonata = work("bnf", "work:2", "Sonatine syncopée")
    mb_sonata = work("musicbrainz", "work:a", "Sonatine syncopee")
    old = work("bnf", "work:3", "Patrie", 1920)
    remake = work("wikidata", "film:Q2", "Patrie", 1946, "film_score")  # years too far apart
    resolve(s)
    g = Groups(s)
    assert g.find(film) == g.find(bnf) and g.find(sonata) == g.find(mb_sonata)
    assert g.find(old) != g.find(remake) and open_reviews(s, kind="unit_same_title") == []
    from maestro.completeness import load_units

    units = load_units(s, g)
    assert units[g.find(film)].tier == "B" and units[g.find(sonata)].tier == "A"
