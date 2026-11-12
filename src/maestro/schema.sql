-- Canon store: thin entities with a claim table underneath.
-- One database per maestro. Every value is a sourced claim; nothing is overwritten.

CREATE TABLE IF NOT EXISTS schema_version (
    version     integer PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

-- Sources registered from the pack. Lineage groups sources that copy each other.
CREATE TABLE IF NOT EXISTS source (
    id           text PRIMARY KEY,
    kind         text NOT NULL CHECK (kind IN ('structured', 'semi', 'prose', 'model', 'human')),
    lineage      text NOT NULL,
    reliability  real NOT NULL CHECK (reliability BETWEEN 0 AND 1),
    publishable  boolean NOT NULL DEFAULT true
);

-- Entities: works, recordings, releases, containers (film, album, session, score), people.
CREATE TABLE IF NOT EXISTS entity (
    id          bigserial PRIMARY KEY,
    type        text NOT NULL CHECK (type IN ('work', 'recording', 'release', 'container', 'person', 'cue', 'lyric', 'song')),
    label       text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS entity_type_idx ON entity (type);

-- Raw cache: every fetched page, so extractors can be re-run without re-crawling.
-- The file itself lives under data/ (git-ignored, private layer).
CREATE TABLE IF NOT EXISTS raw_page (
    id           bigserial PRIMARY KEY,
    url          text NOT NULL,
    fetched_at   timestamptz NOT NULL DEFAULT now(),
    http_status  integer,
    sha256       text NOT NULL,
    path         text NOT NULL
);
CREATE INDEX IF NOT EXISTS raw_page_url_idx ON raw_page (url);

-- Claims: (entity, field, value) with provenance.
--   kind:       observed (read from a source), generated (a model produced it), decided (human or rule)
--   ref_entity: set when the value points at another entity (a credit, membership of a container, same_as)
--   visibility: publishable claims may ship in a FOSS release; private ones never leave the machine
CREATE TABLE IF NOT EXISTS claim (
    id                 bigserial PRIMARY KEY,
    entity_id          bigint NOT NULL REFERENCES entity (id),
    field              text NOT NULL,
    value              jsonb,
    ref_entity_id      bigint REFERENCES entity (id),
    source_id          text NOT NULL REFERENCES source (id),
    extractor          text,
    extractor_version  text,
    retrieved_at       timestamptz NOT NULL DEFAULT now(),
    raw_page_id        bigint REFERENCES raw_page (id),
    confidence         real NOT NULL DEFAULT 1 CHECK (confidence BETWEEN 0 AND 1),
    kind               text NOT NULL DEFAULT 'observed' CHECK (kind IN ('observed', 'generated', 'decided')),
    status             text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded', 'disputed')),
    visibility         text NOT NULL DEFAULT 'publishable' CHECK (visibility IN ('publishable', 'private')),
    CHECK (value IS NOT NULL OR ref_entity_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS claim_entity_field_idx ON claim (entity_id, field) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS claim_ref_idx ON claim (ref_entity_id, field) WHERE ref_entity_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS claim_source_idx ON claim (source_id);

-- Crawl jobs. purpose and gap_id record why each fetch happened.
CREATE TABLE IF NOT EXISTS job (
    id           bigserial PRIMARY KEY,
    url          text NOT NULL,
    extractor    text NOT NULL,
    purpose      text NOT NULL CHECK (purpose IN ('spine', 'container_sweep', 'enrich', 'gap')),
    entity_id    bigint REFERENCES entity (id),
    gap_id       text,
    priority     integer NOT NULL DEFAULT 0,
    status       text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed', 'skipped')),
    attempts     integer NOT NULL DEFAULT 0,
    created_at   timestamptz NOT NULL DEFAULT now(),
    fetched_at   timestamptz,
    raw_page_id  bigint REFERENCES raw_page (id),
    error        text
);
CREATE UNIQUE INDEX IF NOT EXISTS job_open_url_idx ON job (url, extractor) WHERE status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS job_queue_idx ON job (status, priority DESC, id);

-- Human review queue: ambiguous merges, checksum mismatches and other judgement calls.
-- A decision is recorded as a decided claim (source 'user'); the item keeps a pointer to it.
CREATE TABLE IF NOT EXISTS review_item (
    id              bigserial PRIMARY KEY,
    kind            text NOT NULL,
    subject_ids     bigint[] NOT NULL,
    evidence        jsonb NOT NULL DEFAULT '{}'::jsonb,
    priority        integer NOT NULL DEFAULT 0,
    status          text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'decided', 'dismissed')),
    decision        text,
    note            text,
    decided_claim_id bigint REFERENCES claim (id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    decided_at      timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS review_item_subject_idx ON review_item (kind, subject_ids);
CREATE INDEX IF NOT EXISTS review_item_open_idx ON review_item (status, priority DESC, id);

-- Pages loaded by hand (maestro-import), with the extractor options used, so they can be replayed.
CREATE TABLE IF NOT EXISTS import_log (
    id           bigserial PRIMARY KEY,
    raw_page_id  bigint NOT NULL REFERENCES raw_page (id),
    extractor    text NOT NULL,
    options      jsonb NOT NULL DEFAULT '{}'::jsonb,
    imported_at  timestamptz NOT NULL DEFAULT now()
);

INSERT INTO schema_version (version) VALUES (1) ON CONFLICT DO NOTHING;
INSERT INTO schema_version (version) VALUES (2) ON CONFLICT DO NOTHING;
