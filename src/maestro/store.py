"""Canon store: Postgres access for entities, claims, sources and crawl jobs."""

from __future__ import annotations

from importlib import resources
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from maestro.pack import Pack

# Built-in sources for claims the harness itself makes.
BUILTIN_SOURCES = [
    {"id": "claude", "kind": "model", "lineage": "claude", "reliability": 0.5, "publishable": True},
    {"id": "user", "kind": "human", "lineage": "user", "reliability": 1.0, "publishable": True},
]

ENTITY_TYPES = {"work", "recording", "release", "container", "person", "cue", "lyric", "song"}
QUERY_ROW_LIMIT = 500


class StoreError(RuntimeError):
    pass


class Store:
    def __init__(self, conn: psycopg.Connection, pack: Pack):
        self.conn = conn
        self.pack = pack

    @classmethod
    def connect(cls, dsn: str, pack: Pack) -> "Store":
        conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=True)
        if conn.info.encoding != "utf-8":
            # SQL_ASCII databases hand text back as bytes and cannot be trusted with Devanagari or Urdu.
            enc = conn.info.encoding
            conn.close()
            raise StoreError(f"database encoding is {enc!r}; create it with ENCODING 'UTF8' (e.g. TEMPLATE template0)")
        return cls(conn, pack)

    def close(self) -> None:
        self.conn.close()

    # ---- setup -------------------------------------------------------------

    def migrate(self) -> None:
        sql = resources.files("maestro").joinpath("schema.sql").read_text("utf-8")
        with self.conn.transaction():
            self.conn.execute(sql)

    def sync_sources(self) -> int:
        """Register the pack's sources (and the built-in ones). Returns how many were written."""
        rows = [*BUILTIN_SOURCES, *self.pack.data["sources"]]
        with self.conn.transaction():
            for s in rows:
                self.conn.execute(
                    """INSERT INTO source (id, kind, lineage, reliability, publishable)
                       VALUES (%(id)s, %(kind)s, %(lineage)s, %(reliability)s, %(publishable)s)
                       ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, lineage = EXCLUDED.lineage,
                         reliability = EXCLUDED.reliability, publishable = EXCLUDED.publishable""",
                    {**s, "publishable": s.get("publishable", True)},
                )
        return len(rows)

    def maestro_entity(self) -> int:
        """The person entity for this maestro, created on first use."""
        row = self.conn.execute(
            """SELECT entity_id FROM claim WHERE field = 'is_maestro' AND status = 'active' LIMIT 1"""
        ).fetchone()
        if row:
            return row["entity_id"]
        eid = self.create_entity("person", self.pack.data["identity"]["name"])
        self.upsert_claim(eid, "is_maestro", value=True, source_id="user", kind="decided")
        return eid

    # ---- writes ------------------------------------------------------------

    def create_entity(self, type: str, label: str) -> int:
        if type not in ENTITY_TYPES:
            raise StoreError(f"unknown entity type {type!r}; expected one of {sorted(ENTITY_TYPES)}")
        row = self.conn.execute(
            "INSERT INTO entity (type, label) VALUES (%s, %s) RETURNING id", (type, label)
        ).fetchone()
        return row["id"]

    def upsert_claim(
        self,
        entity_id: int,
        field: str,
        *,
        source_id: str,
        value: Any = None,
        ref_entity_id: int | None = None,
        confidence: float = 1.0,
        kind: str = "observed",
        extractor: str | None = None,
        extractor_version: str | None = None,
        raw_page_id: int | None = None,
        private: bool = False,
    ) -> int:
        """Add a claim. An identical active claim from the same source is reused, not duplicated.

        Claims from a non-publishable source are always private.
        """
        if value is None and ref_entity_id is None:
            raise StoreError("a claim needs a value or a ref_entity_id")
        src = self.conn.execute("SELECT publishable FROM source WHERE id = %s", (source_id,)).fetchone()
        if src is None:
            raise StoreError(f"unknown source {source_id!r}; add it to the pack and run sync_sources")
        visibility = "private" if private or not src["publishable"] else "publishable"
        jvalue = Jsonb(value) if value is not None else None

        existing = self.conn.execute(
            """SELECT id FROM claim
               WHERE entity_id = %s AND field = %s AND source_id = %s AND status = 'active'
                 AND value IS NOT DISTINCT FROM %s AND ref_entity_id IS NOT DISTINCT FROM %s""",
            (entity_id, field, source_id, jvalue, ref_entity_id),
        ).fetchone()
        if existing:
            return existing["id"]
        row = self.conn.execute(
            """INSERT INTO claim (entity_id, field, value, ref_entity_id, source_id, extractor,
                                  extractor_version, raw_page_id, confidence, kind, visibility)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (entity_id, field, jvalue, ref_entity_id, source_id, extractor, extractor_version,
             raw_page_id, confidence, kind, visibility),
        ).fetchone()
        return row["id"]

    def entity_by_xref(self, source_id: str, external_id: str, type: str | None = None) -> int | None:
        """The entity a source knows by `external_id` (recorded as an `xref` claim), if any.

        Sources often number films, people and songs independently, so pass `type` whenever it is known:
        film 4357 and person 4357 are different entities.
        """
        row = self.conn.execute(
            """SELECT c.entity_id FROM claim c JOIN entity e ON e.id = c.entity_id
               WHERE c.field = 'xref' AND c.source_id = %s AND c.value = %s AND c.status = 'active'
                 AND (%s::text IS NULL OR e.type = %s)
               ORDER BY c.id LIMIT 1""",
            (source_id, Jsonb(str(external_id)), type, type),
        ).fetchone()
        return row["entity_id"] if row else None

    def find_or_create_by_xref(self, type: str, source_id: str, external_id: str, label: str) -> int:
        """Get or create the entity a source identifies by `external_id`, recording the xref claim."""
        eid = self.entity_by_xref(source_id, external_id, type)
        if eid is None:
            eid = self.create_entity(type, label)
            self.upsert_claim(eid, "xref", value=str(external_id), source_id=source_id)
        return eid

    def merge_entities(self, keep_id: int, merge_id: int, reason: str, source_id: str = "claude") -> int:
        """Record that merge_id is the same entity as keep_id. Reversible: supersede the claim to undo."""
        if keep_id == merge_id:
            raise StoreError("cannot merge an entity with itself")
        return self.upsert_claim(
            merge_id, "same_as", ref_entity_id=keep_id, value={"reason": reason},
            source_id=source_id, kind="decided",
        )

    def enqueue_crawl(
        self,
        url: str,
        extractor: str,
        purpose: str,
        *,
        entity_id: int | None = None,
        gap_id: str | None = None,
        priority: int = 0,
    ) -> dict[str, Any]:
        """Queue a crawl job. Returns the job, or the already-open job for the same url and extractor.

        Refuses once the queue holds the pack's per-round budget of open jobs.
        """
        open_job = self.conn.execute(
            "SELECT * FROM job WHERE url = %s AND extractor = %s AND status IN ('queued', 'running')",
            (url, extractor),
        ).fetchone()
        if open_job:
            return {**open_job, "already_queued": True}
        budget = self.pack.data["budget"]["crawl_requests_per_round"]
        queued = self.conn.execute("SELECT count(*) AS n FROM job WHERE status = 'queued'").fetchone()["n"]
        if queued >= budget:
            raise StoreError(f"crawl budget reached: {queued} jobs queued (budget {budget} per round)")
        return self.conn.execute(
            """INSERT INTO job (url, extractor, purpose, entity_id, gap_id, priority)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
            (url, extractor, purpose, entity_id, gap_id, priority),
        ).fetchone()

    def next_job(self) -> dict[str, Any] | None:
        """Claim the highest-priority queued job (marks it running)."""
        with self.conn.transaction():
            return self.conn.execute(
                """UPDATE job SET status = 'running', attempts = attempts + 1
                   WHERE id = (SELECT id FROM job WHERE status = 'queued'
                               ORDER BY priority DESC, id LIMIT 1 FOR UPDATE SKIP LOCKED)
                   RETURNING *"""
            ).fetchone()

    def finish_job(self, job_id: int, *, status: str, raw_page_id: int | None = None, error: str | None = None) -> None:
        if status not in ("done", "failed", "skipped", "queued"):
            raise StoreError(f"bad job status {status!r}")
        self.conn.execute(
            """UPDATE job SET status = %s, raw_page_id = coalesce(%s::bigint, raw_page_id), error = %s,
                              fetched_at = CASE WHEN %s::bigint IS NULL THEN fetched_at ELSE now() END
               WHERE id = %s""",
            (status, raw_page_id, error, raw_page_id, job_id),
        )

    def record_raw_page(self, url: str, http_status: int, sha256: str, path: str) -> int:
        return self.conn.execute(
            "INSERT INTO raw_page (url, http_status, sha256, path) VALUES (%s, %s, %s, %s) RETURNING id",
            (url, http_status, sha256, path),
        ).fetchone()["id"]

    # ---- reads -------------------------------------------------------------

    def query_graph(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a read-only SQL query against the store (at most QUERY_ROW_LIMIT rows)."""
        with self.conn.transaction():
            self.conn.execute("SET TRANSACTION READ ONLY")
            cur = self.conn.execute(sql, params or {})
            rows = cur.fetchmany(QUERY_ROW_LIMIT) if cur.description else []
        return rows

    def _canon_units_sql(self) -> str:
        # A canon unit is an entity of the pack's unit type, not merged away, with an active
        # credit to the maestro in an in-scope role.
        return """
            SELECT DISTINCT e.id, e.label
            FROM entity e
            JOIN claim c ON c.entity_id = e.id AND c.field = 'credit' AND c.status = 'active'
            WHERE e.type = %(unit)s
              AND c.ref_entity_id = %(maestro)s
              AND c.value ->> 'role' = ANY(%(roles)s)
              AND NOT EXISTS (SELECT 1 FROM claim s WHERE s.entity_id = e.id
                              AND s.field = 'same_as' AND s.status = 'active')
        """

    def _canon_params(self) -> dict[str, Any]:
        return {
            "unit": self.pack.unit,
            "maestro": self.maestro_entity(),
            "roles": self.pack.data["roles"]["in_scope"],
        }

    def coverage_report(self) -> dict[str, Any]:
        """Observed canon units per tier, and how many source lineages have seen them.

        Estimated totals (capture-recapture) arrive with the completeness engine in a later phase.
        """
        params = self._canon_params()
        rows = self.conn.execute(
            f"""WITH units AS ({self._canon_units_sql()})
                SELECT coalesce(t.value #>> '{{}}', 'unassigned') AS tier, count(*) AS observed
                FROM units u
                LEFT JOIN claim t ON t.entity_id = u.id AND t.field = 'tier' AND t.status = 'active'
                GROUP BY 1 ORDER BY 1""",
            params,
        ).fetchall()
        by_tier = {r["tier"]: r["observed"] for r in rows}
        tiers = {
            name: {"label": spec["label"], "observed": by_tier.pop(name, 0), "target_missing": spec["target_missing"]}
            for name, spec in self.pack.tiers.items()
        }
        lineages = self.conn.execute(
            f"""WITH units AS ({self._canon_units_sql()})
                SELECT s.lineage, count(DISTINCT c.entity_id) AS units
                FROM units u JOIN claim c ON c.entity_id = u.id AND c.status = 'active'
                JOIN source s ON s.id = c.source_id
                GROUP BY s.lineage ORDER BY units DESC""",
            params,
        ).fetchall()
        return {
            "maestro": self.pack.slug,
            "unit": self.pack.unit,
            "tiers": tiers,
            "unassigned": by_tier.get("unassigned", 0),
            "units_per_lineage": {r["lineage"]: r["units"] for r in lineages},
        }

    def find_gaps(self, limit: int = 50) -> list[dict[str, Any]]:
        """Ranked gaps the orchestrator can turn into crawl jobs."""
        params = {**self._canon_params(), "limit": limit}
        gaps: list[dict[str, Any]] = []

        if self.pack.data["canon_model"]["container_has_full_listing"]:
            # Containers that credit the maestro in an in-scope role but hold no canon units yet.
            for r in self.conn.execute(
                f"""WITH units AS ({self._canon_units_sql()})
                    SELECT DISTINCT e.id, e.label FROM entity e
                    JOIN claim c ON c.entity_id = e.id AND c.field = 'credit' AND c.status = 'active'
                         AND c.ref_entity_id = %(maestro)s AND c.value ->> 'role' = ANY(%(roles)s)
                    WHERE e.type = 'container'
                      AND NOT EXISTS (SELECT 1 FROM claim m JOIN units u ON u.id = m.entity_id
                                      WHERE m.field = 'part_of' AND m.ref_entity_id = e.id
                                        AND m.status = 'active')
                    ORDER BY e.id LIMIT %(limit)s""",
                params,
            ).fetchall():
                gaps.append({
                    "gap_id": f"empty-container:{r['id']}", "kind": "empty_container",
                    "entity_id": r["id"], "label": r["label"], "priority": 3,
                    "action": "fetch this container's full listing",
                })

        # Canon units seen by only one source lineage: weakly confirmed, and the f1 term in Chao1.
        for r in self.conn.execute(
            f"""WITH units AS ({self._canon_units_sql()})
                SELECT u.id, u.label, min(s.lineage) AS lineage
                FROM units u JOIN claim c ON c.entity_id = u.id AND c.status = 'active'
                JOIN source s ON s.id = c.source_id AND s.kind IN ('structured', 'semi', 'prose')
                GROUP BY u.id, u.label HAVING count(DISTINCT s.lineage) = 1
                ORDER BY u.id LIMIT %(limit)s""",
            params,
        ).fetchall():
            gaps.append({
                "gap_id": f"single-lineage:{r['id']}", "kind": "single_lineage",
                "entity_id": r["id"], "label": r["label"], "priority": 2,
                "action": f"confirm from a source outside lineage {r['lineage']!r}",
            })

        # Canon units with no tier assigned.
        for r in self.conn.execute(
            f"""WITH units AS ({self._canon_units_sql()})
                SELECT u.id, u.label FROM units u
                WHERE NOT EXISTS (SELECT 1 FROM claim t WHERE t.entity_id = u.id
                                  AND t.field = 'tier' AND t.status = 'active')
                ORDER BY u.id LIMIT %(limit)s""",
            params,
        ).fetchall():
            gaps.append({
                "gap_id": f"no-tier:{r['id']}", "kind": "no_tier",
                "entity_id": r["id"], "label": r["label"], "priority": 1,
                "action": "assign a tier from the pack's tier definitions",
            })

        gaps.sort(key=lambda g: -g["priority"])
        return gaps[:limit]
