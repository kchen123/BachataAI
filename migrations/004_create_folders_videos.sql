CREATE TABLE IF NOT EXISTS bachata.folders (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  parent_id INT REFERENCES bachata.folders(id) ON DELETE CASCADE,
  position INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bachata.videos (
  id SERIAL PRIMARY KEY,
  title TEXT NOT NULL,
  filename TEXT,
  folder_id INT REFERENCES bachata.folders(id) ON DELETE SET NULL,
  gcs_url TEXT,
  duration_sec FLOAT,
  fps FLOAT,
  frame_count INT,
  status TEXT DEFAULT 'uploaded',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_folders_parent ON bachata.folders(parent_id);
CREATE INDEX IF NOT EXISTS idx_videos_folder ON bachata.videos(folder_id);
