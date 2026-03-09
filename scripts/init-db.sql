-- Initialize all databases for miran-service
-- Runs once on first PostgreSQL start (via docker-entrypoint-initdb.d)

SELECT 'CREATE DATABASE ocr_service' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ocr_service')\gexec
SELECT 'CREATE DATABASE agent_memory' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'agent_memory')\gexec
SELECT 'CREATE DATABASE prefect'      WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'prefect')\gexec
