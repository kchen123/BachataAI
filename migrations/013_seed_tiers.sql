INSERT INTO bachata.tiers (name, month_rev, year_rev, metadata) VALUES
  ('Free',       0.00,   0.00, '{"minutes": 10, "description": "Get started with basic analysis.", "features": ["Basic scores", "Watermarked exports", "Pose overlay"]}'),
  ('Dancer',    19.99, 149.99, '{"minutes": 60, "description": "For social dancers practising weekly.", "features": ["Full scoring", "Clean exports", "Weight shift analysis", "Speed graphs"]}'),
  ('Pro',       39.99, 299.99, '{"minutes": 150, "description": "For competitors and serious students.", "features": ["Everything in Dancer", "Custom scorecards", "Body focus isolation", "Progress tracking"]}')
ON CONFLICT (name) DO NOTHING;
