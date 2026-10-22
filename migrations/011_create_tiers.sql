CREATE TABLE IF NOT EXISTS bachata.tiers (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  month_rev NUMERIC(10,2),
  year_rev NUMERIC(10,2),
  metadata JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
