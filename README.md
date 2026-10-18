# Vehicle safety pipeline

A daily data pipeline on US vehicle safety data from NHTSA (the National Highway Traffic Safety Administration). Owner complaints and recall notices land in S3, load into Snowflake, and dbt models them through bronze, silver and gold layers. Two AI features will sit on the gold layer: a chatbot that answers questions from complaint and recall text and cites the records it used, and a text-to-SQL assistant over the gold tables.

The project is being built over about six weeks. Week 1 is done: infrastructure, ingestion and the first load into Snowflake. Everything below marked "planned" isn't built yet.

## Architecture

```
NHTSA bulk files ──▶ S3 bronze (.txt.gz, partitioned by load date)
                         │  COPY INTO
                         ▼
                Snowflake BRONZE (latest snapshot)
                         │  dbt
                         ▼
                SILVER: typed, deduplicated, standardized make/model/component
                         │  dbt
                         ▼
                GOLD: marts for analysis and the AI layer
                         │
          ┌──────────────┴───────────────┐
          ▼                              ▼
   RAG chatbot                     Text-to-SQL
   embeddings in a Snowflake       question → SQL on GOLD →
   VECTOR column, answers cite     read-only role → answer
   complaint and recall IDs        with the SQL shown
          └──────────────┬───────────────┘
                         ▼
                   Streamlit app

Airflow, daily: check for new files → S3 → COPY → dbt build → embed new text → health checks
```

## Stack

| Layer | Tool | Status |
|---|---|---|
| Storage | AWS S3 (Sydney), lifecycle rule deletes snapshots after 60 days | Built |
| Infrastructure | Terraform for S3, IAM, the AWS budget and SNS alerts | Built |
| Ingestion | Python: ETag check, download, MD5 check, gzip, upload | Built |
| Warehouse | Snowflake, two X-Small warehouses with resource monitors | Built |
| Transformation | dbt Core, incremental models for the large tables | Planned (week 2) |
| Orchestration | Airflow via Astronomer Cosmos, on an EC2 Spot instance | Planned (weeks 3 and 6) |
| Embeddings and retrieval | Snowflake Cortex embeddings (Voyage AI as fallback), hand-written cosine similarity search with metadata filters | Planned (week 4) |
| LLM | Claude API | Planned (weeks 4 and 5) |
| App | Streamlit Community Cloud | Planned (weeks 4 and 5) |
| CI | GitHub Actions | Planned (week 6) |

## The data

NHTSA publishes each dataset as a zipped, tab-delimited file that is regenerated every day. Each file is a full snapshot, not a daily increment. Sizes from the 18 Oct 2026 files:

| Dataset | Zip | Rows |
|---|---|---|
| Complaints (since 1995) | 375 MB | 2,257,425 |
| Recalls since 2010 | 15 MB | 246,109 |
| Recalls before 2010 | 7.5 MB | 81,710 |
| Investigations | 4.3 MB | 154,439 |

Some of what profiling found, which shapes the silver layer (full notes in [docs/data_profile.md](docs/data_profile.md)):

- A complaint has one row per component it mentions, so 2.26 million rows cover 1.63 million complaints. The row ID (`CMPLID`) can change between files according to NHTSA's own spec, so silver keys on the complaint number plus component instead.
- About 17,000 complaints have different description text on different rows. NHTSA's spec says that before December 2002 one complaint number could cover several products, so silver needs a rule for which text to keep.
- Since a 2021 system change, blank yes/no fields show as `N` and blank counts as `0`, so "no injuries" and "not reported" look the same.
- Make and model names need standardizing before they can be grouped or queried reliably. NHTSA's API, for example, searches Tesla's Model Y as `MODEL Y (All Variants)`, while the bulk file stores it as `MODEL Y` and, in one complaint, `ROBOTAXI (MODEL Y)`.
- The complaint file has a vehicle operator name field (added April 2026, empty so far) and dealer contact details. Silver drops those fields so they never reach the app or the language model.

## How ingestion works

[`ingestion/nhtsa_ingest.py`](ingestion/nhtsa_ingest.py) sends a HEAD request for each file first. NHTSA's `ETag` header is the zip's MD5, so an unchanged ETag means the file can be skipped. When a file has changed, the script downloads it, checks the MD5, and re-compresses the text inside as gzip, because Snowflake's `COPY` can't read zip files. It uploads the result to `s3://<bucket>/bronze/<dataset>/load_date=YYYY-MM-DD/` and writes a small manifest. If the lifecycle rule has deleted the last copy, the script uploads the file again even if the ETag hasn't changed.

Snowflake's bronze tables hold only the latest snapshot. Each load empties and refills a table inside one transaction, so a failed load leaves the previous day's data in place. Earlier snapshots stay in S3.

## Cost

The target is under USD 40 a month once everything runs daily. The pieces that keep it there:

- one X-Small warehouse for the pipeline and one for the app, each suspending after 60 seconds idle, with resource monitors that stop them at a credit limit
- Airflow on an EC2 Spot instance instead of on-demand or managed Airflow
- caching app query results for 24 hours, since the data changes once a day
- prompt caching and per-session limits on the Claude API

Measured costs will go here once the pipeline runs daily.

## Repository layout

```
infra/       Terraform: S3, IAM, budget, SNS. Setup steps in infra/README.md
ingestion/   Download and S3 upload, with unit tests
snowflake/   Setup and bronze load SQL
docs/        Data profile and NHTSA field descriptions
```

dbt, Airflow, the AI code and the app will be added in later weeks.

## Running it

Setup is in [infra/README.md](infra/README.md): Terraform first, then the Snowflake scripts in order, then ingestion and the bronze load. Tests for the ingestion code:

```
cd ingestion
pip install -r requirements.txt
pytest
```

## Data source

All data comes from NHTSA's public datasets at `https://static.nhtsa.gov/odi/ffdd/` and needs no API key. NHTSA's field descriptions are copied in [docs/nhtsa/](docs/nhtsa/).
