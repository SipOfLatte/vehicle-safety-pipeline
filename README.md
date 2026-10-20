# Vehicle safety pipeline

A daily data pipeline on US vehicle safety data from NHTSA (the National Highway Traffic Safety Administration). Owner complaints and recall notices land in S3, load into Snowflake, and dbt models them through bronze, silver and gold layers. Two AI features sit on the gold layer: a chatbot that answers questions from complaint and recall text and cites the records it used, and a text-to-SQL assistant over the gold tables.

The project is being built over about six weeks. Weeks 1 to 5 are done: infrastructure, ingestion, the load into Snowflake, the dbt silver and gold layers, an Airflow DAG that runs it all daily (locally for now), the RAG chatbot and the text-to-SQL assistant (both running locally, not deployed yet). Everything below marked "planned" isn't built yet.

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
| Embeddings and retrieval | Snowflake Cortex embeddings (`snowflake-arctic-embed-l-v2.0`), hand-written cosine similarity search with metadata filters | Built |
| LLM | Claude API: Claude Haiku 5.5 for the chatbot, Claude Sonnet 5.5 for text-to-SQL | Built |
| App | Streamlit: Chat and Ask pages built and tested locally | Deploy to Streamlit Community Cloud planned (week 6) |
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

Gold holds the tables the app and the text-to-SQL assistant query. NHTSA publishes no sales figures, so complaint totals are counts, not rates per vehicle sold: a best-seller like the F-150 collects more complaints simply because more of them are on the road. There's no free per-model sales or registration dataset with a clear licence, so the tables lead with `severe_share` instead, the share of a vehicle's own complaints that report a crash, fire, injury or death, which doesn't depend on sales volume. Ranked that way, the 2023 list starts with a BMW iX (38 complaints), and the Tesla Model 3 (428 complaints) comes seventh.

| Model | What it answers | Rows (20 Oct 2026) |
|---|---|---|
| `fct_complaints_monthly` | Complaints per month, vehicle and component group, with crash, fire, injury and death counts | 1,387,542 |
| `vehicle_complaint_summary` | Per make, model family and model year: share of complaints that report a crash, fire, injury or death, complaint count with its percentile among the same model year, recall campaigns | 31,992 |
| `recall_coverage` | Complaints and recalls side by side per vehicle and component group, to find complaint clusters with no matching recall | 198,244 |
| `component_trends` | Monthly complaints per vehicle and component group against the average of the three months before, with a flag for sharp rises | 90,632 |
| `rag_documents` | Text for the chatbot: 427,782 complaints received since 2020 and 13,689 vehicle recalls since 2010 | 441,471 |

Two examples of what they show. For 2018 Ford Escapes, 1,444 complaints are about the engine and no recall matches that model year and component group. In September 2026, Ford Edge engine complaints reached 98, against an average of 35 over the previous three months. A recall can still cover a problem under a different component group, so `recall_coverage` points at places to look rather than proving a gap.

The descriptions in `dbt/models/gold/_gold.yml` are written for the text-to-SQL assistant, since they are its only description of the tables.

## RAG chatbot

The chatbot answers questions about specific vehicles from 441,471 documents: owner complaints received since 2020 and vehicle recalls since 2010. Each document is embedded with Snowflake Cortex (`snowflake-arctic-embed-l-v2.0`, 1,024 dimensions) by an incremental dbt model, so the daily run only embeds new or changed documents. The one-time backfill took 72 seconds on an X-Small warehouse and about 70 million tokens.

A question goes through three steps:

1. Claude Haiku 5.5 turns it into filters (make, model, model year, complaint or recall) as structured output. Each value is checked against names that exist in the data; an unknown name is dropped and shown to the user rather than silently filtering out every document.
2. One SQL query applies the filters, then ranks documents by `VECTOR_COSINE_SIMILARITY` to the question's embedding. Without a document-type filter it keeps 3 complaints and 2 recalls, because complaints are written in the first person like questions are and otherwise crowd recalls out of the top 5.
3. Claude Haiku answers from those 5 documents only and cites them by ID. The app links each ID to NHTSA's record and flags any citation that wasn't among the retrieved documents.

The app limits each session to 10 questions and caps Claude spend per page each day, tracked in Snowflake: USD 0.20 for the chatbot and USD 0.30 for text-to-SQL, so one page can't use up the other's share. One chatbot question costs about USD 0.0006.

### Evaluation

[`ai/evals/rag_questions.yaml`](ai/evals/rag_questions.yaml) has 30 questions with a known right answer, scored on whether it appears in the top 5:

| Set | Questions | Hit rate at 5 |
|---|---|---|
| Specific recalls (used while tuning retrieval) | 10 | 10/10 |
| Complaint clusters (one vehicle, year and component) | 10 | 10/10 |
| Held-out recalls (written after tuning, run once) | 10 | 9/10 |

The held-out set is the honest number. Its one miss was the electric Dodge Charger. The model filter matched `CHARGER`, but NHTSA files the electric car as `CHARGER EV`, so the right recall was filtered out. The filter now also searches a model's electric and hybrid families, such as `CHARGER EV`, `KONA ELECTRIC` and `RAV4 PRIME`, and the Charger question hits. That fix came after the held-out run, so 9/10 stays the reported score.

Every question was checked by hand before scoring. Five tuning questions were reworded because they copied the recall's wording or got a detail wrong. Eight now also accept a second recall that answers them equally well, often a re-recall of the same defect. Two complaint questions require a keyword, such as "phantom" for phantom braking, because their component group covers far more than the question asks about. Complaint clusters are still easier to hit than recalls, since hundreds of complaints count as correct. Across all 30 answers, no answer cited a document it wasn't given.

Two things the evaluation changed. Component group was a filter at first, but the model's guess at NHTSA's categories was wrong twice in ten (backup cameras are filed under back-over prevention, axle shafts under power train) and shut out the right recall, so it's now only shown, not applied. And a first version embedded the question inside the search query; Snowflake inlined that call into the sort and ran it once per row, about 441,000 Cortex calls per unfiltered search. The question is now embedded once in its own query, and an unfiltered search takes about a second instead of fifty.

## Text-to-SQL assistant

The Ask page answers questions about numbers: how many complaints a vehicle has, which makes lead a component group, where complaint clusters have no recall. Claude Sonnet 5.5 sees only the four gold analytics tables, described from the dbt docs YAML, and works with two tools:

- `find_values` looks up how a make, model or component group is stored, so "Silverado" becomes `SILVERADO 1500` before it goes into a filter.
- `run_sql` runs a query and returns the first 30 rows. If Snowflake rejects the query, the error goes back to Claude, which fixes it and tries again.

A question takes 2 or 3 Claude calls (at most 6), about 5 seconds and USD 0.015. The app shows the answer, the final SQL, the result table, and a chart when the result is a time series or a short ranking.

### Keeping model-written SQL read-only

Before a query runs, [`guard.py`](ai/text_to_sql/guard.py) parses it with sqlglot and rejects anything that isn't a single SELECT on the four tables, along with functions such as `RESULT_SCAN` and Cortex AI calls. The query then runs as `ASK_ROLE`, which can read those four tables and nothing else, with a 30-second timeout and a 1,000-row cap.

Testing the role turned up two ways around it. Snowflake gives new users `DEFAULT_SECONDARY_ROLES = ('ALL')`, so a session running as `ASK_ROLE` also carried the app user's other role and could read and write the app's usage log. Snowflake also grants Cortex to the `PUBLIC` role, which every role inherits, so a SELECT could call paid AI functions. The app user now has no secondary roles, Cortex is granted only to the two roles that use it, and the guard blocks those calls as well.

### Evaluation

[`ai/evals/sql_questions.yaml`](ai/evals/sql_questions.yaml) has 40 questions, each with a reference query written and checked by hand. A question passes when Claude's final query returns the same rows as the reference, with both run at the same moment because the data changes daily. Column names and order don't matter, and numbers match within 1%. Three questions ask for things the tables don't hold (sales, prices, Australian data). They pass only if Claude runs no query and says it can't answer.

| Set | Questions | Execution accuracy |
|---|---|---|
| Tuning | 25 | 24/25 |
| Held out (run once after tuning) | 15 | 15/15 |

The first tuning run scored 16/25. Most misses had the right answer in the text but returned more rows than the question asked for, because the prompt said to show a top 10 when no number was given. After that prompt change, a fix to the scorer and a reworded ambiguous question, one miss remains. Asked what owners complain about most on the 2018 Chevrolet Equinox, Claude names service brakes but returns a ranking of every component group instead of one row. The held-out questions are mostly direct lookups and rankings, so 15/15 is closer to an upper bound than a typical score. The review reworded one held-out question and tightened the pass rule for unanswerable ones, and those two held-out questions were re-run once.

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
ai/          RAG retrieval and answers, text-to-SQL agent, eval questions and runners
app/         Streamlit app
docs/        Data profile and NHTSA field descriptions
```

## Running it

Setup is in [infra/README.md](infra/README.md): Terraform first, then the Snowflake scripts in order, then ingestion and the bronze load. Tests for the ingestion code:

```
cd ingestion
pip install -r requirements.txt
pytest
```

## Data source

All data comes from NHTSA's public datasets at `https://static.nhtsa.gov/odi/ffdd/` and needs no API key. NHTSA's field descriptions are copied in [docs/nhtsa/](docs/nhtsa/).
