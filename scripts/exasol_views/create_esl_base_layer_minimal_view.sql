-- ESL minimal load-test surface
--
-- Purpose:
--   Keep the ESL-generated query path intentionally small for targeted
--   concurrency tests by exposing only "Calendar: Year" from BASE_LAYER.
--
-- Run:
--   scripts/exasol_views/run_esl_base_layer_minimal_view.sh --profile xyz-pilot-target-sys

CREATE OR REPLACE VIEW <SEMANTIC_LAYER_SCHEMA>.BASE_LAYER_MINIMAL AS
SELECT "Calendar: Year"
FROM <SEMANTIC_LAYER_SCHEMA>.BASE_LAYER;

GRANT SELECT ON <SEMANTIC_LAYER_SCHEMA>.BASE_LAYER_MINIMAL TO XYZ_BIZ, XYZ_DASH;
