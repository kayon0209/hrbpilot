-- Local Docker bootstrap: keep the application role non-superuser so RLS is effective.
CREATE ROLE hrbp WITH LOGIN PASSWORD 'hrbp_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
GRANT ALL PRIVILEGES ON DATABASE hrbp_workbench TO hrbp;
GRANT ALL ON SCHEMA public TO hrbp;
