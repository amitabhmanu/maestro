"""Lyric words over time: spelling keys, Devanagari, counting, waves, and that output holds counts only."""

from collections import Counter

from maestro.lyricwords import (Lexicon, build, check_publishable, devanagari_to_latin, key, script_of, waves,
                                words_of)


def test_key_folds_spellings():
    assert key("zindagee") == key("Zindagi") == key("jindagi")
    assert key("taqadeer") == key("taqdeer")
    assert key("aapakee") == key("aapki")
    assert key("lagata") == key("lagta")
    assert key("chaand") != key("chanda")


def test_devanagari():
    assert devanagari_to_latin("दिल") == "dil"
    assert devanagari_to_latin("प्यार") == "pyaar"
    assert key(devanagari_to_latin("ज़िंदगी")) == key("zindagi")
    assert key(devanagari_to_latin("मोहब्बत")) == key("mohabbat")
    assert script_of("दिल की बात") == "devanagari"
    assert script_of("dil ki baat") == "latin"


def test_words_of_cuts_notes_and_opening():
    assert words_of("Dil Ki Baat (Part 2) . . . more", opening_only=True) == ["dil", "ki", "baat"]


def _songs(word, years, filler_years):
    out = []
    i = 0
    for y in filler_years:
        out.append({"id": i, "year": y, "container": i, "text": f"filler{i % 7} line", "kind": "lyrics"})
        i += 1
    for y in years:
        out.append({"id": i, "year": y, "container": i, "text": f"{word} song{i % 5}", "kind": "lyrics"})
        i += 1
    return out


def test_waves_find_a_word_that_fades():
    years = Counter({y: 100 for y in range(1950, 1980)})
    per = {"haya": Counter({1960: 6, 1961: 7, 1962: 5, 1950: 1, 1975: 1}),
           "dil": Counter({y: 10 for y in range(1950, 1980)})}
    found = [w["word"] for w in waves(years, per, stop=set())]
    assert found == ["haya"]


def test_build_publishes_counts_only():
    songs = _songs("haya", [1960] * 10 + [1961] * 10, [y for y in range(1950, 1980) for _ in range(20)])
    data = build(songs, Lexicon(concepts={"x": {"label": "X", "words": ["haya"]}}), maestro={"slug": "t"},
                 min_songs=8)
    assert data["words"][key("haya")]["label"] == "haya"
    assert data["concepts"]["x"]["words"]["haya"]["years"]["1960"] == 10
    assert not check_publishable(data, 8)
    assert all(sum(w["years"].values()) >= 8 for w in data["words"].values())
