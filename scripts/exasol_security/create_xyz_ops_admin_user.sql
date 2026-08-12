-- Create/rotate XYZ_OPS database admin account (non-SYS) for <PILOT_NAME>.
--
-- Purpose:
--   Provide a broad operations/admin DB account for XYZ operators so day-to-day
--   administration does not require SYS credential disclosure.
--
-- Safety boundary:
--   This script intentionally does NOT grant direct user-management powers
--   (`CREATE USER`, `ALTER USER`, `DROP USER`) to XYZ_OPS, so XYZ_OPS cannot
--   delete/remove SYS or manage principal lifecycle.
--
-- Run as SYS (example):
--   cat scripts/exasol_security/create_xyz_ops_admin_user.sql | exapump sql --profile xyz-pilot
--
-- Password bootstrap:
--   1) Replace <SET-STRONG-PASSWORD> before first run
--   2) Store same value in SSM SecureString:
--      /xyz-pilot/secrets/exasol-xyz-ops

CREATE USER XYZ_OPS IDENTIFIED BY "<SET-STRONG-PASSWORD>";

-- Session + broad operational visibility.
GRANT CREATE SESSION TO XYZ_OPS;
GRANT SELECT ANY DICTIONARY TO XYZ_OPS;
GRANT EXPORT TO XYZ_OPS;
GRANT IMPORT TO XYZ_OPS;
GRANT KILL ANY SESSION TO XYZ_OPS;
GRANT ALTER SYSTEM TO XYZ_OPS;

-- Broad object administration for pilot schemas.
GRANT ALL PRIVILEGES ON SCHEMA <PILOT_SCHEMA> TO XYZ_OPS;
GRANT ALL PRIVILEGES ON SCHEMA <SEMANTIC_LAYER_SCHEMA> TO XYZ_OPS;
GRANT ALL PRIVILEGES ON SCHEMA ESL_POC_LOGICAL_MODEL TO XYZ_OPS;
GRANT ALL PRIVILEGES ON SCHEMA ESL_POC_PHYSICAL_MODEL TO XYZ_OPS;
GRANT ALL PRIVILEGES ON SCHEMA ESL_POC_RELATIONAL_MODEL TO XYZ_OPS;

-- Explicitly grant the <SEMANTIC_LAYER_VENDOR> business role too, so diagnostics against semantic
-- views are available without using SYS.
GRANT ESL_POC_ROLE_BUSINESS TO XYZ_OPS;

-- Defensive deny: keep account/user lifecycle restricted to SYS operators.
REVOKE CREATE USER FROM XYZ_OPS;
REVOKE ALTER USER FROM XYZ_OPS;
REVOKE DROP USER FROM XYZ_OPS;
