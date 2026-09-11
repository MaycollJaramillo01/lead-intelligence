-- Key/value metadata about this local analytical store (e.g. when it was initialised).
CREATE TABLE store_metadata (
    key VARCHAR PRIMARY KEY CHECK (length(key) BETWEEN 1 AND 128),
    value VARCHAR NOT NULL,
    updated_at VARCHAR NOT NULL
);
