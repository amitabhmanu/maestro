"""Lyrics policy: the store never holds more lyric text than the pack's `legal.lyrics` allows in its
publishable layer.

A pack names the claim fields whose values quote lyrics in `legal.lyric_fields` (for Hindi film songs
the title is the song's opening words, and catalogues such as HFGK give the whole opening, up to
200 characters and more). The fields `lyrics` and `lyric` always count. For a dict value (a popularity
`mention`), the keys named in `lyric_fields` are the lyric text.

What `Store.upsert_claim` does with lyric text longer than its first line (`first_line`):

- `none`: `lyrics`/`lyric` fields are refused (a pack with `lyrics: none` lists no lyric_fields).
- `first_line_only`, purpose `personal` or `personal_then_foss`: the claim is kept as given but made
  private, and a publishable copy holding only the first line is added: a `first_line` claim for a text
  field, or the same field with the lyric keys cut for a dict (the full dict then goes to `<field>_full`,
  private), so scoring that reads publishable claims still sees it.
- `first_line_only`, purpose `public`: only the first-line copy is stored.
- `private_full`: the claim is made private (packs allow this only for purpose `personal`).

`maestro-lyrics` checks a store (and explorer snapshots) for lyric text beyond the first line in the
publishable layer, and with `--fix` brings claims stored before this guard in line (superseding them).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ALWAYS_LYRIC_FIELDS = ("lyrics", "lyric")
FIRST_LINE_MAX = 80
_BREAK = re.compile(r"\s*(?:\r?\n|\.{2,}|…|\s/\s)\s*")


def first_line(text: str, max_chars: int = FIRST_LINE_MAX) -> str:
    """The opening line of a lyric text: up to the first line break or ellipsis, then at most
    `max_chars`, cut at the last comma (else space) before the limit."""
    line = _BREAK.split(text.strip(), maxsplit=1)[0].strip()
    if len(line) > max_chars:
        cut = line[:max_chars]
        at = cut.rfind(",")
        if at < max_chars // 3:
            at = cut.rfind(" ")
        line = cut[:at] if at > 0 else cut
    return line.rstrip(" ,;:-–—")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().rstrip(" ,;:-–—.…")


def beyond_first_line(text: str, max_chars: int = FIRST_LINE_MAX) -> bool:
    return isinstance(text, str) and _norm(first_line(text, max_chars)) != _norm(text)


class LyricsRefused(ValueError):
    pass


@dataclass
class Plan:
    """How to store one claim: `keep` as given (made private if `private`), plus an optional publishable
    `extra` claim (field, value) carrying only first lines. `store_original=False` drops the original."""
    private: bool = False
    extra: tuple[str, Any] | None = None
    store_original: bool = True
    field: str | None = None  # the original's field, when it moves (dict values go to <field>_full)


class Policy:
    def __init__(self, pack_data: dict[str, Any]):
        legal = pack_data["legal"]
        self.mode = legal["lyrics"]
        self.purpose = legal["purpose"]
        self.fields = set(legal.get("lyric_fields", []))
        self.max_chars = legal.get("first_line_max", FIRST_LINE_MAX)

    def lyric_parts(self, field: str, value: Any) -> list[str]:
        """The lyric texts inside a claim value (empty when the claim carries none)."""
        if field in ALWAYS_LYRIC_FIELDS:
            return [value] if isinstance(value, str) else [json.dumps(value, ensure_ascii=False)]
        if field in self.fields and isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [v for k, v in value.items() if k in self.fields and isinstance(v, str)]
        return []

    def too_long(self, field: str, value: Any) -> bool:
        return field in ALWAYS_LYRIC_FIELDS or any(
            beyond_first_line(t, self.max_chars) for t in self.lyric_parts(field, value))

    def cut(self, value: Any) -> Any:
        if isinstance(value, str):
            return first_line(value, self.max_chars)
        return {k: first_line(v, self.max_chars) if k in self.fields and isinstance(v, str) else v
                for k, v in value.items()}

    def plan(self, field: str, value: Any) -> Plan:
        if value is None or not self.lyric_parts(field, value):
            return Plan()
        if field in ALWAYS_LYRIC_FIELDS and self.mode == "none":
            raise LyricsRefused(f"this pack's legal.lyrics is 'none': a {field!r} claim cannot be stored")
        if not self.too_long(field, value):
            return Plan()
        if self.mode == "private_full":
            return Plan(private=True)
        short = self.cut(value) if field not in ALWAYS_LYRIC_FIELDS else None
        if isinstance(value, dict):
            extra = (field, short)
            moved = f"{field}_full"
        else:
            extra = ("first_line", short) if short else None
            moved = None
        if self.purpose == "public":
            return Plan(store_original=False, extra=extra)
        return Plan(private=True, extra=extra, field=moved)


# ---- the publish check ----------------------------------------------------------------------------

def check_store(store, policy: Policy | None = None, limit: int = 20) -> dict[str, Any]:
    """Publishable claims that carry lyric text beyond the first line, or that come from a source the
    pack does not mark publishable."""
    policy = policy or Policy(store.pack.data)
    fields = sorted(policy.fields | set(ALWAYS_LYRIC_FIELDS))
    rows = store.conn.execute(
        """SELECT c.id, c.entity_id, c.field, c.value FROM claim c
           WHERE c.status = 'active' AND c.visibility = 'publishable'
             AND (c.field = ANY(%s) OR jsonb_typeof(c.value) = 'object')""", (fields,)).fetchall()
    bad = [r for r in rows if policy.lyric_parts(r["field"], r["value"]) and policy.too_long(r["field"], r["value"])]
    leaked = store.conn.execute(
        """SELECT count(*) AS n FROM claim c JOIN source s ON s.id = c.source_id
           WHERE c.visibility = 'publishable' AND NOT s.publishable""").fetchone()["n"]
    lyric_units = store.conn.execute(
        """SELECT count(*) AS n FROM claim c JOIN entity e ON e.id = c.entity_id
           WHERE e.type = 'lyric' AND c.visibility = 'publishable' AND c.status = 'active'""").fetchone()["n"]
    by_field: dict[str, int] = {}
    for r in bad:
        by_field[r["field"]] = by_field.get(r["field"], 0) + 1
    return {"maestro": store.pack.slug, "lyrics": policy.mode, "lyric_fields": fields,
            "beyond_first_line": len(bad), "by_field": by_field,
            "sample": [{"claim": r["id"], "field": r["field"], "value": r["value"]} for r in bad[:limit]],
            "from_private_sources": leaked, "lyric_entity_claims": lyric_units,
            "ok": not bad and not leaked and not lyric_units}


def fix_store(store, policy: Policy | None = None) -> dict[str, int]:
    """Bring publishable claims stored before the guard in line: each offending claim is superseded and
    written again through `upsert_claim`, which applies the policy (private original, first-line copy)."""
    policy = policy or Policy(store.pack.data)
    report = check_store(store, policy, limit=10**9)
    ids = [s["claim"] for s in report["sample"]]
    if not ids:
        return {"fixed": 0}
    rows = store.conn.execute("SELECT * FROM claim WHERE id = ANY(%s) ORDER BY id", (ids,)).fetchall()
    with store.conn.transaction():
        for r in rows:
            store.conn.execute("UPDATE claim SET status = 'superseded' WHERE id = %s", (r["id"],))
            store.upsert_claim(r["entity_id"], r["field"], value=r["value"], ref_entity_id=r["ref_entity_id"],
                               source_id=r["source_id"], confidence=r["confidence"], kind=r["kind"],
                               extractor=r["extractor"], extractor_version=r["extractor_version"],
                               raw_page_id=r["raw_page_id"])
    return {"fixed": len(rows)}


def check_snapshot(path: Path, policy: Policy) -> dict[str, Any]:
    """An explorer snapshot (canon/<slug>.json) must hold no lyric text beyond first lines."""
    data = json.loads(Path(path).read_text("utf-8"))
    bad: list[str] = []

    def walk(node: Any, key: str | None = None) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and key in {"titles", "title", "title_alt_script", "lyrics", "lyric"} \
                and (policy.fields or key in ALWAYS_LYRIC_FIELDS) and beyond_first_line(node, policy.max_chars):
            bad.append(node)

    walk(data)
    return {"snapshot": str(path), "beyond_first_line": len(bad), "sample": bad[:10], "ok": not bad}


def main(argv: list[str] | None = None) -> int:
    """`maestro-lyrics`: check that no lyric text beyond the first line is publishable.

    maestro-lyrics --pack maestros/rafi                 check the store (exit 1 if anything is found)
    maestro-lyrics --pack maestros/rafi --fix           supersede offending claims and store them again
                                                         under the policy (private original, first line public)
    maestro-lyrics --pack maestros/rafi --snapshot data/explorer/canon/rafi.json
    """
    from maestro import config
    from maestro.pack import load_pack
    from maestro.store import Store

    p = argparse.ArgumentParser(description=main.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pack", required=True)
    p.add_argument("--dsn")
    p.add_argument("--fix", action="store_true")
    p.add_argument("--snapshot", type=Path, action="append", default=[], help="also check an explorer snapshot")
    p.add_argument("--no-store", action="store_true", help="check only the snapshots")
    args = p.parse_args(argv)
    pack = load_pack(args.pack)
    policy = Policy(pack.data)
    results: list[dict[str, Any]] = []
    if not args.no_store:
        dsn = config.dsn_for(pack.slug, args.dsn)
        if not dsn:
            p.error(f"--dsn (or {config.dsn_env_name(pack.slug)} or MAESTRO_DSN) is required")
        store = Store.connect(dsn, pack)
        store.migrate()
        if args.fix:
            with store.exclusive():
                print(json.dumps(fix_store(store, policy)))
        results.append(check_store(store, policy, limit=5))
        store.close()
    results += [check_snapshot(s, policy) for s in args.snapshot]
    print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
