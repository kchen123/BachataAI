CREATE TABLE IF NOT EXISTS bachata.user_tiers (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES bachata.users(id) ON DELETE CASCADE,
  tier_id INT NOT NULL REFERENCES bachata.tiers(id) ON DELETE CASCADE,
  metadata JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_user_tiers_user ON bachata.user_tiers(user_id);
CREATE INDEX IF NOT EXISTS idx_user_tiers_tier ON bachata.user_tiers(tier_id);
