"""Lyrics policy: first lines, the store guard, the publish check and fix, explorer snapshots."""

import copy
import json

import pytest
from conftest import ROOT
from test_resolve import song

from maestro.explore import snapshot
from maestro.lyrics import (
    Policy,
    beyond_first_line,
    check_snapshot,
    check_store,
    first_line,
    fix_store,
    main,
)
from maestro.pack import load_pack, validate
from maestro.store import StoreError

LONG = ("Chaudhvin Ka Chand Ho Ya Aaftab Ho, Jo Bhi Ho Tum Khuda Ki Qasam Lajawab Ho, "
        "Zulfen Hain Jaise Kandhon Pe Baadal Jhuke Hue, Aankhen Hain Jaise Mai Ke Pyaale Bhare Hue")


def _policy(**legal):
    return Policy({"legal": {"purpose": "personal_then_foss", "lyrics": "first_line_only", **legal}})


def test_first_line():
    assert first_line("Ye Zindagi Ke Mele") == "Ye Zindagi Ke Mele"
    assert first_line("Kahin Door Jab Din Dhal Jaaye\nSaanjh Ki Dulhan") == "Kahin Door Jab Din Dhal Jaaye"
    assert first_line("Suhani Raat Dhal Chuki... Na Jaane Tum") == "Suhani Raat Dhal Chuki"
    assert first_line("Line one / line two") == "Line one"
    assert first_line(LONG) == "Chaudhvin Ka Chand Ho Ya Aaftab Ho, Jo Bhi Ho Tum Khuda Ki Qasam Lajawab Ho"
    assert len(first_line("word " * 40)) <= 80
    assert not beyond_first_line("Ye Zindagi Ke Mele...")
    assert beyond_first_line(LONG)


def test_plan():
    p = _policy(lyric_fields=["title"])
    assert p.plan("title", "Short title").private is False
    plan = p.plan("title", LONG)
    assert plan.private and plan.extra == ("first_line", first_line(LONG)) and plan.store_original
    mention = {"signal": "esteem", "title": LONG, "note": "a classic"}
    plan = p.plan("mention", mention)
    assert plan.field == "mention_full" and plan.extra[0] == "mention"
    assert plan.extra[1]["title"] == first_line(LONG) and plan.extra[1]["note"] == "a classic"
    public = Policy({"legal": {"purpose": "public", "lyrics": "first_line_only", "lyric_fields": ["title"]}})
    assert public.plan("title", LONG).store_original is False
    assert _policy().plan("title", LONG).private is False          # titles are not lyrics unless listed
    assert _policy().plan("lyrics", "La la la").private is True     # lyrics fields always are
    with pytest.raises(ValueError, match="'none'"):
        Policy({"legal": {"purpose": "personal", "lyrics": "none"}}).plan("lyrics", "La")
    assert Policy({"legal": {"purpose": "personal", "lyrics": "private_full", "lyric_fields": ["title"]}}) \
        .plan("title", LONG).private


def test_pack_rejects_lyric_fields_without_lyrics():
    data = copy.deepcopy(load_pack(ROOT / "maestros" / "jean-wiener").data)
    assert validate(data) == []
    data["legal"]["lyric_fields"] = ["title"]
    assert any("lyric_fields" in p for p in validate(data))


def test_store_refuses_lyrics_when_pack_allows_none(store_for):
    s = store_for("jean-wiener")
    eid = s.create_entity("work", "Une chanson")
    with pytest.raises(StoreError, match="'none'"):
        s.upsert_claim(eid, "lyrics", value="Toute la chanson", source_id="user")


def _claims(s, eid, field=None):
    rows = s.conn.execute("SELECT field, value, visibility FROM claim WHERE entity_id = %s AND status = 'active'"
                          + (" AND field = %s" if field else "") + " ORDER BY id",
                          (eid, field) if field else (eid,)).fetchall()
    return [(r["field"], r["value"], r["visibility"]) for r in rows]


def test_store_keeps_long_titles_private_with_a_first_line(store_for):
    s = store_for("rafi")
    u = song(s, "hfgk_online", "1", "Chaudhvin Ka Chand")
    s.upsert_claim(u, "title", value=LONG, source_id="hfgk_online")
    s.upsert_claim(u, "title", value=LONG, source_id="hfgk_online")         # again: nothing new
    s.upsert_claim(u, "title", value="Chaudhvin Ka Chand Ho", source_id="musicbrainz")
    assert _claims(s, u, "title") == [("title", LONG, "private"), ("title", "Chaudhvin Ka Chand Ho", "publishable")]
    assert _claims(s, u, "first_line") == [("first_line", first_line(LONG), "publishable")]

    s.upsert_claim(s.maestro_entity(), "mention", value={"signal": "esteem", "title": LONG, "note": "n"},
                   source_id="hfgk_online")
    cut = _claims(s, s.maestro_entity(), "mention")
    assert len(cut) == 1 and cut[0][1]["title"] == first_line(LONG) and cut[0][2] == "publishable"
    full = _claims(s, s.maestro_entity(), "mention_full")
    assert len(full) == 1 and full[0][1]["title"] == LONG and full[0][2] == "private"
    assert check_store(s)["ok"]


def test_public_pack_stores_only_the_first_line(store_for):
    s = store_for("rafi")
    s.lyrics = Policy({"legal": {"purpose": "public", "lyrics": "first_line_only", "lyric_fields": ["title"]}})
    u = song(s, "hfgk_online", "1", "Chaudhvin Ka Chand")
    s.upsert_claim(u, "title", value=LONG, source_id="hfgk_online")
    assert _claims(s, u, "title") == []
    assert _claims(s, u, "first_line") == [("first_line", first_line(LONG), "publishable")]


def test_check_and_fix_claims_stored_before_the_guard(store_for):
    s = store_for("rafi")
    u = song(s, "hfgk_online", "1", "Chaudhvin Ka Chand")
    s.upsert_claim(u, "title", value="placeholder", source_id="hfgk_online")
    s.conn.execute("UPDATE claim SET value = %s WHERE entity_id = %s AND field = 'title' AND value = '\"placeholder\"'",
                   (json.dumps(LONG), u))
    report = check_store(s)
    assert not report["ok"] and report["beyond_first_line"] == 1 and report["by_field"] == {"title": 1}
    assert fix_store(s) == {"fixed": 1}
    assert check_store(s)["ok"]
    assert _claims(s, u, "first_line") == [("first_line", first_line(LONG), "publishable")]
    assert ("title", LONG, "private") in _claims(s, u, "title")
    assert fix_store(s) == {"fixed": 0}


def test_snapshot_carries_only_first_lines(store_for, tmp_path):
    s = store_for("rafi")
    u = song(s, "hfgk_online", "1", LONG)                     # the entity label is the long title too
    s.upsert_claim(u, "title", value=LONG, source_id="hfgk_online")
    snap = snapshot(s)
    unit = next(x for x in snap["units"] if x["id"] == u)
    assert unit["title"] == first_line(LONG) and LONG not in json.dumps(snap, ensure_ascii=False)
    path = tmp_path / "rafi.json"
    path.write_text(json.dumps(snap, ensure_ascii=False, default=str), "utf-8")
    assert check_snapshot(path, s.lyrics)["ok"]
    leaky = snapshot(s, include_private=True)
    path.write_text(json.dumps(leaky, ensure_ascii=False, default=str), "utf-8")
    assert not check_snapshot(path, s.lyrics)["ok"]
    assert main(["--pack", str(ROOT / "maestros" / "rafi"), "--no-store", "--snapshot", str(path)]) == 1
