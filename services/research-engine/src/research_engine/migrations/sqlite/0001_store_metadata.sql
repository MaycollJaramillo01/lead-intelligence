-- Key/value metadata about this local operational store (e.g. when it was initialised).
CREATE TABLE store_metadata (
    key TEXT PRIMARY KEY NOT NULL CHECK (length(key) BETWEEN 1 AND 128),
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;
