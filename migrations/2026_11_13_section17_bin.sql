-- Section 17 BIN lookup: split the two 3DS channels and drop the old cache.
--
-- UP
--   1. bin_3ds becomes the advisory probe channel (probe_* columns + source).
--   2. bin_3ds_passive is the authoritative channel written by real checks.
--   3. bin_cache is gone; bin_record is the record now.
--
-- DOWN
--   Recreates bin_cache and collapses bin_3ds back to its pre-split shape.
--
-- Postgres only. The application still calls Base.metadata.create_all on a
-- fresh database; this migration is for an existing one.

BEGIN;

-- 1. Probe channel ---------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'bin_3ds'
          AND column_name = 'reads'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'bin_3ds'
          AND column_name = 'probe_reads'
    ) THEN
        ALTER TABLE bin_3ds RENAME TO bin_3ds_legacy;

        CREATE TABLE bin_3ds (
            bin VARCHAR(8) PRIMARY KEY,
            probe_reads INTEGER NOT NULL DEFAULT 0,
            probe_challenges INTEGER NOT NULL DEFAULT 0,
            probe_last_at TIMESTAMPTZ NULL,
            probe_last_state VARCHAR(8) NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'probe'
        );

        -- The old rows were produced by probes, so they belong to this channel.
        INSERT INTO bin_3ds (
            bin, probe_reads, probe_challenges, probe_last_at,
            probe_last_state, source
        )
        SELECT bin, reads, challenges, last_at, last_state, 'probe'
        FROM bin_3ds_legacy;

        DROP TABLE bin_3ds_legacy;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS bin_3ds (
    bin VARCHAR(8) PRIMARY KEY,
    probe_reads INTEGER NOT NULL DEFAULT 0,
    probe_challenges INTEGER NOT NULL DEFAULT 0,
    probe_last_at TIMESTAMPTZ NULL,
    probe_last_state VARCHAR(8) NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'probe'
);

-- 2. Passive channel (authoritative) --------------------------------------
CREATE TABLE IF NOT EXISTS bin_3ds_passive (
    bin VARCHAR(8) PRIMARY KEY,
    reads INTEGER NOT NULL DEFAULT 0,
    challenges INTEGER NOT NULL DEFAULT 0,
    last_at TIMESTAMPTZ NULL,
    last_state VARCHAR(8) NOT NULL DEFAULT ''
);

-- 3. Legacy cache ----------------------------------------------------------
DROP TABLE IF EXISTS bin_cache;

COMMIT;


-- DOWN --------------------------------------------------------------------
-- Run this block to roll the schema back. It restores the cache table and the
-- pre-split bin_3ds columns; the probe-only columns cannot be represented in
-- the old shape, so they are copied into reads/challenges.

BEGIN;

CREATE TABLE IF NOT EXISTS bin_cache (
    bin VARCHAR(8) PRIMARY KEY,
    json JSONB NOT NULL,
    sources INTEGER NOT NULL DEFAULT 0,
    confidence INTEGER NOT NULL DEFAULT 0,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'bin_3ds'
          AND column_name = 'probe_reads'
    ) THEN
        ALTER TABLE bin_3ds RENAME TO bin_3ds_probe;

        CREATE TABLE bin_3ds (
            bin VARCHAR(8) PRIMARY KEY,
            reads INTEGER NOT NULL DEFAULT 0,
            challenges INTEGER NOT NULL DEFAULT 0,
            last_at TIMESTAMPTZ NULL,
            last_state VARCHAR(8) NOT NULL DEFAULT ''
        );

        INSERT INTO bin_3ds (bin, reads, challenges, last_at, last_state)
        SELECT bin, probe_reads, probe_challenges, probe_last_at, probe_last_state
        FROM bin_3ds_probe;

        DROP TABLE bin_3ds_probe;
    END IF;
END $$;

DROP TABLE IF EXISTS bin_3ds_passive;

COMMIT;
