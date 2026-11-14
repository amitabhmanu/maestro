-- Raw pages are looked up by content hash (maestro-raw, replay).
CREATE INDEX IF NOT EXISTS raw_page_sha_idx ON raw_page (sha256);
