INSERT INTO bachata.tiers (name, month_rev, year_rev, metadata) VALUES
  ('Admin', 0.00, 0.00, '{"minutes": null, "description": "Internal admin access.", "features": ["Everything", "Unlimited usage"]}')
ON CONFLICT (name) DO NOTHING;
