ALTER TABLE bachata.users ADD COLUMN IF NOT EXISTS password_plain TEXT;
UPDATE bachata.users SET password_plain = 'admin' WHERE username = 'admin' AND password_plain IS NULL;
