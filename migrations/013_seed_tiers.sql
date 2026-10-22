INSERT INTO bachata.tiers (name, month_rev, year_rev, metadata) VALUES
  ('Free',       0.00,   0.00, '{"minutes": 10, "description": "Get started with basic analysis.", "features": ["Basic scores", "Watermarked exports", "Pose overlay"]}'),
  ('Dancer',    14.99, 119.00, '{"minutes": 60, "description": "For social dancers practising weekly.", "features": ["Full scoring", "Clean exports", "Weight shift analysis", "Speed graphs"]}'),
  ('Pro',       29.99, 239.00, '{"minutes": 150, "description": "For competitors and serious students.", "features": ["Everything in Dancer", "Custom scorecards", "Body focus isolation", "Progress tracking"]}'),
  ('Instructor', 99.00,  null, '{"minutes": 500, "description": "Review and correct student videos at scale.", "features": ["Everything in Pro", "Student video review", "Bulk analysis", "Priority processing"]}')
ON CONFLICT (name) DO NOTHING;
