-- Databases and extensions Lemma needs before anything else starts.
--
-- Runs once, from the Postgres image's /docker-entrypoint-initdb.d, on an empty
-- data directory. That timing is the point: initdb runs against a server bound
-- to a local socket only, before the container starts accepting connections, so
-- every database below exists before SuperTokens or the migration job can look
-- for one.
--
-- The dev stack does this imperatively instead (`make _ensure-databases`), and
-- pays for it: it creates the `supertokens` database after SuperTokens has
-- already started and failed, so it has to restart the container afterwards.
-- Nothing here needs that.
--
-- `lemma` itself is created by the entrypoint from POSTGRES_DB.

CREATE DATABASE supertokens;

-- Pod data lives in its own database, not another schema. See
-- docs/configuration.md; DATASTORE_DATABASE_URL points here.
CREATE DATABASE lemma_datastore;

-- pgvector, in both databases that store embeddings. The baseline migration
-- assumes the extension is already present.
\connect lemma
CREATE EXTENSION IF NOT EXISTS vector;

\connect lemma_datastore
CREATE EXTENSION IF NOT EXISTS vector;
