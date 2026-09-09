CREATE SCHEMA IF NOT EXISTS bachata;

CREATE TABLE IF NOT EXISTS bachata.videos (
  id SERIAL PRIMARY KEY,
  filename TEXT NOT NULL,
  original_name TEXT NOT NULL,
  duration_sec FLOAT,
  fps FLOAT,
  frame_count INT,
  status TEXT DEFAULT 'uploaded',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bachata.analyses (
  id SERIAL PRIMARY KEY,
  video_id INT REFERENCES bachata.videos(id) ON DELETE CASCADE,
  model TEXT DEFAULT 'mediapipe_pose',
  status TEXT DEFAULT 'pending',
  summary JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bachata.joint_frames (
  id SERIAL PRIMARY KEY,
  analysis_id INT REFERENCES bachata.analyses(id) ON DELETE CASCADE,
  frame_num INT NOT NULL,
  timestamp_sec FLOAT NOT NULL,
  landmarks JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_joint_frames_analysis ON bachata.joint_frames(analysis_id);
CREATE INDEX IF NOT EXISTS idx_analyses_video ON bachata.analyses(video_id);
