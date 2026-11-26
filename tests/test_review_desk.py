"""The review desk page and its answers."""

import json

from maestro.review_desk import answers_for, build_page, suggest_groups, version_markers


def song(i, label):
    return {"id": i, "type": "song", "label": label, "sources": ["hfgk_online"], "records": 1, "facts": {}}


def test_suggest_groups_follows_the_version_rule():
    assert version_markers("Dil Ka Haal Sune Dilwala (Part 2)") == {"part": "2"}
    # Similar titles join; a copy that names no version joins the first group it fits.
    assert suggest_groups([song(1, "Pyar Hua Iqrar Hua"), song(2, "Pyar Hua Iqarar Hua, Hai Pyar Se")]) == [[1, 2]]
    # Different parts or voices stay apart.
    g = suggest_groups([song(1, "Tera Saath Hai To - Female"), song(2, "Tera Saath Hai To - Male"), song(3, "Tera Sath Hai To")])
    assert sorted(map(sorted, g)) == [[1, 3], [2]]
    assert suggest_groups([{"id": 1, "type": "person", "label": "A"}, {"id": 2, "type": "person", "label": "A"}]) == [[1], [2]]


def test_page_and_answers_round_trip():
    ex = {"maestro": "rafi", "questions": [{"id": 7, "kind": "unit_near_title", "priority": 1, "floating": None,
                                            "members": [song(10, "Ya Meri Manzil Bata"), song(11, "Ya Meree Manzil Bata </script>")],
                                            "pairs": [{"item": 7, "kind": "unit_near_title", "subjects": [10, 11],
                                                       "evidence": {"similarity": 0.95}}]}]}
    html = build_page([ex], "twins_round1", "Duplicates")
    assert "<title>Duplicates</title>" in html and "</script>\"" not in html
    payload = json.loads(html.split('type="application/json">')[1].split("</script>")[0])
    q = payload["maestros"]["rafi"][0]
    assert q["items"] == [7] and q["suggest"] == [[10, 11]] and payload["round"] == "twins_round1"
    rows = [{"id": "rafi_7", "data": {"maestro": "rafi", "q": "rafi:7", "items": [7], "choice": "groups",
                                      "groups": [[10], [11]], "at": "2026-11-26T10:00:00Z"}},
            {"id": "rafi_7", "data": {"maestro": "rafi", "q": "rafi:7", "items": [7], "choice": "groups",
                                      "groups": [[10, 11]], "at": "2026-11-26T11:00:00Z"}},
            {"id": "lata_1", "data": {"maestro": "lata", "q": "lata:1", "items": [1], "choice": "skip"}}]
    assert answers_for(rows, "rafi")["answers"] == [{"items": [7], "groups": [[10, 11]], "note": "review desk"}]
    assert answers_for(rows, "lata")["answers"] == []
