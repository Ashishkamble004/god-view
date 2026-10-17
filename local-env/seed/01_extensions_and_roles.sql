-- PostgreSQL 16 extensions and Data Agent v1 read-isolation role (Build Decision A12)
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'godview_readonly') THEN
    CREATE ROLE godview_readonly WITH LOGIN PASSWORD 'local_readonly_password_only';
  END IF;
END
$$;

ALTER ROLE godview_readonly SET default_transaction_read_only = on;
ALTER ROLE godview_readonly SET statement_timeout = '5s';

GRANT CONNECT ON DATABASE godview_lms TO godview_readonly;
GRANT USAGE ON SCHEMA public TO godview_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO godview_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO godview_readonly;
