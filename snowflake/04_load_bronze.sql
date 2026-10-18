-- Manual load of the latest snapshot into bronze (week 1). Airflow takes this over in week 3.
-- Replace <LOAD_DATE> with the load_date folder that ingestion printed (YYYY-MM-DD).
--
-- NHTSA publishes full snapshots, so bronze holds only the latest one and S3 keeps the
-- history. Each table is emptied and reloaded inside one transaction, so a failed COPY
-- leaves yesterday's data in place. FORCE = TRUE lets a same-day rerun load the file again.

USE ROLE PIPELINE_ROLE;
USE WAREHOUSE PIPELINE_WH;
USE SCHEMA VEHICLE_SAFETY.BRONZE;

-- Check the files are visible first.
LIST @NHTSA_STAGE/complaints/load_date=<LOAD_DATE>/;

BEGIN;
DELETE FROM COMPLAINTS;
COPY INTO COMPLAINTS
FROM (
  SELECT $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17,
         $18, $19, $20, $21, $22, $23, $24, $25, $26, $27, $28, $29, $30, $31, $32, $33,
         $34, $35, $36, $37, $38, $39, $40, $41, $42, $43, $44, $45, $46, $47, $48, $49,
         $50, $51,
         METADATA$FILENAME, CURRENT_TIMESTAMP()
  FROM @NHTSA_STAGE/complaints/load_date=<LOAD_DATE>/
)
FORCE = TRUE
ON_ERROR = ABORT_STATEMENT;
COMMIT;

BEGIN;
DELETE FROM RECALLS;
COPY INTO RECALLS
FROM (
  SELECT $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17,
         $18, $19, $20, $21, $22, $23, $24, $25, $26, $27, $28, $29,
         METADATA$FILENAME, CURRENT_TIMESTAMP()
  FROM @NHTSA_STAGE/
)
PATTERN = '.*(recalls_post_2010|recalls_pre_2010)/load_date=<LOAD_DATE>/.*[.]txt[.]gz'
FORCE = TRUE
ON_ERROR = ABORT_STATEMENT;
COMMIT;

-- Row counts from the 18 Oct 2026 files, for comparison:
-- complaints 2,257,425 rows; recalls 246,109 (post-2010) + 81,710 (pre-2010) = 327,819.
SELECT 'complaints' AS t, COUNT(*) FROM COMPLAINTS
UNION ALL SELECT 'recalls', COUNT(*) FROM RECALLS;
