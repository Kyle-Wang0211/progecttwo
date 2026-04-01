DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aether') THEN
        CREATE ROLE aether LOGIN PASSWORD 'aetherpass';
    END IF;
END
$$;
