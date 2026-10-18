-- Check that Cortex embeddings work on this account. Trial accounts are blocked; this needs a payment method.
-- Expect a 1024-dimension vector. If this errors, the fallback is the Voyage AI API.

USE ROLE PIPELINE_ROLE;
USE WAREHOUSE PIPELINE_WH;

SELECT VECTOR_COSINE_SIMILARITY(
  SNOWFLAKE.CORTEX.EMBED_TEXT_1024('snowflake-arctic-embed-l-v2.0', 'brakes failed while driving on the highway'),
  SNOWFLAKE.CORTEX.EMBED_TEXT_1024('snowflake-arctic-embed-l-v2.0', 'service brakes stopped working at speed')
) AS similarity;  -- should be well above 0.5
