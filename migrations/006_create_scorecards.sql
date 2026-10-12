CREATE TABLE IF NOT EXISTS bachata.scorecards (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES bachata.users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  metadata JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bachata.scorecard_metrics (
  id SERIAL PRIMARY KEY,
  scorecard_id INT NOT NULL REFERENCES bachata.scorecards(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  range JSONB DEFAULT '{}',
  metadata JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scorecards_user ON bachata.scorecards(user_id);
CREATE INDEX IF NOT EXISTS idx_scorecard_metrics_scorecard ON bachata.scorecard_metrics(scorecard_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scorecards_user_name ON bachata.scorecards(user_id, name);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scorecard_metrics_scorecard_name ON bachata.scorecard_metrics(scorecard_id, name);
