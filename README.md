# Vehicle safety pipeline

A daily data pipeline on US vehicle safety data from NHTSA (the National Highway Traffic Safety Administration). Owner complaints and recall notices land in S3, load into Snowflake, and dbt models them through bronze, silver and gold layers. Two AI features will sit on the gold layer: a chatbot that answers questions from complaint and recall text and cites the records it used, and a text-to-SQL assistant over the gold tables.

The project is being built over about six weeks. Weeks 1 to 3 are done: infrastructure, ingestion, the load into Snowflake, the dbt silver and gold layers, and an Airflow DAG that runs it all daily (locally for now). Everything below marked "planned" isn't built yet.

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
| Transformation | dbt Core: silver and gold layers, incremental models for the large tables | Built |
| Orchestration | Airflow 3 via Astronomer Cosmos, failure alerts by email through SNS | Built and running locally; EC2 Spot planned (week 6) |
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

## Silver layer

The dbt project in [`dbt/`](dbt/) turns the raw rows into seven tables:

| Model | Grain | Rows (19 Oct 2026) |
|---|---|---|
| `complaints` | one complaint | 1,626,788 |
| `complaint_components` | complaint × product × component | 2,242,415 |
| `recalls` | one recall campaign | 30,378 |
| `recall_components` | campaign × product × vehicle × component | 195,355 |
| `vehicle_name_map` | one reported make/model name | 28,834 |
| `dim_vehicle` | standardized make × model × model year | 87,594 |
| `dim_component` | one component name, split into its hierarchy | 819 |

`complaints` and `complaint_components` are incremental. NHTSA sends a full snapshot every day, so each run hashes every complaint's source rows and merges only the complaints that are new or changed. The hash leaves out `CMPLID`, which NHTSA can renumber without anything else changing. A rerun on the same snapshot changes 0 rows. Because each snapshot is complete, a complaint that NHTSA removes is deleted from silver after the next run, and a test checks that silver and bronze hold exactly the same complaints. The delete is skipped if bronze looks incomplete (fewer than 95% of silver's complaints), so a bad load can't empty silver.

Make and model names are standardized in `vehicle_name_map`, using rules rather than a hand-written list:

- Spellings that differ only in spaces or punctuation become the most common spelling: `F150` becomes `F-150`, `4 RUNNER` becomes `4RUNNER`, `MERCEDES BENZ` becomes `MERCEDES-BENZ`.
- A model family drops trailing powertrain and drivetrain words, so `CAMRY HYBRID` stays a model but belongs to the family `CAMRY`.
- A small seed file overrides the cases the rules get wrong, such as `ROBOTAXI (MODEL Y)`.

These rules change the name on 2.4% of complaint and recall rows. Component names are grouped through another seed that maps NHTSA's 54 top-level component names to 28 groups, for example `SERVICE BRAKES` and `SERVICE BRAKES, HYDRAULIC`. dbt runs 46 tests on the silver layer and its sources: keys, relationships between tables, accepted values and year ranges.

## Gold layer

Gold holds the tables the app and the text-to-SQL assistant query. NHTSA publishes no sales figures, so everything here is a count of complaints, not a rate per vehicle sold.

| Model | What it answers | Rows (20 Oct 2026) |
|---|---|---|
| `fct_complaints_monthly` | Complaints per month, vehicle and component group, with crash, fire, injury and death counts | 1,387,542 |
| `vehicle_risk_summary` | Per make, model family and model year: complaints, share that report a crash, fire, injury or death, recall campaigns | 31,992 |
| `recall_coverage` | Complaints and recalls side by side per vehicle and component group, to find complaint clusters with no matching recall | 198,244 |
| `component_trends` | Monthly complaints per vehicle and component group against the average of the three months before, with a flag for sharp rises | 90,632 |
| `rag_documents` | Text for the chatbot: 427,782 complaints received since 2020 and 13,689 vehicle recalls since 2010 | 441,471 |

Two examples of what they show. For 2018 Ford Escapes, 1,444 complaints are about the engine and no recall matches that model year and component group. In September 2026, Ford Edge engine complaints reached 98, against an average of 35 over the previous three months. A recall can still cover a problem under a different component group, so `recall_coverage` points at places to look rather than proving a gap.

The descriptions in `dbt/models/gold/_gold.yml` are written for the text-to-SQL assistant, since they will be its only description of the tables.

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
dbt/         dbt project: silver and gold models, seeds and tests
airflow/     Astro project: the daily and weekly DAGs, DAG tests
docs/        Data profile and NHTSA field descriptions
```

The AI code and the app will be added in later weeks.

## Running it

Setup is in [infra/README.md](infra/README.md): Terraform first, then the Snowflake scripts in order, then ingestion and the bronze load. Tests for the ingestion code:

```
cd ingestion
pip install -r requirements.txt
pytest
```

## Data source

All data comes from NHTSA's public datasets at `https://static.nhtsa.gov/odi/ffdd/` and needs no API key. NHTSA's field descriptions are copied in [docs/nhtsa/](docs/nhtsa/).
