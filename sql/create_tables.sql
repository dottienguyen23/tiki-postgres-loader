CREATE TABLE IF NOT EXISTS products (
    id          BIGINT PRIMARY KEY,
    name        TEXT,
    url_key     TEXT,
    price       NUMERIC,
    description TEXT,
    images      JSONB,
    loaded_at   TIMESTAMPTZ DEFAULT now()
);