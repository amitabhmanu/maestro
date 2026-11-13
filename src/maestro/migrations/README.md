# Schema migrations

`schema.sql` is the baseline (schema versions 1 and 2) and is safe to re-run. Every later change is a
file here named `NNN_short_name.sql` (NNN = the new schema version, three digits, never reused or edited
after it ships). `Store.migrate()` (run by every command, or on its own with `maestro-migrate`) applies
the files whose version is not yet in `schema_version`, in order, each in the same transaction as its
version row, under an advisory lock so two sessions cannot migrate at once.

Write migrations that are safe on a store with data: add columns with defaults, backfill in the same
file, never drop or rewrite claims (claims are superseded, not deleted).
