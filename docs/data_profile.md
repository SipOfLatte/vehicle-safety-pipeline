# NHTSA flat files: what's in them

Profiled on 19 Oct 2026 from the files NHTSA published on 18 Oct 2026. NHTSA's own field descriptions are in `docs/nhtsa/` (CMPL.txt, RCL.txt, INV.txt), copied from `https://static.nhtsa.gov/odi/ffdd/<dataset>/<NAME>.txt`.

## Format

All four files are tab-delimited with no header row. Every row decoded as UTF-8, and every row has the documented number of columns, so there are no embedded tabs or line breaks in text fields.

Double quotes appear inside text as ordinary characters (172,349 complaint rows have one), so the Snowflake file format turns quote handling and backslash escaping off. Complaints and investigations use LF line endings and recalls use CRLF. The pre-2010 recall file has 5 blank lines, which `SKIP_BLANK_LINES` handles.

Each file is a full snapshot: complaints since 1 Jan 1995, recalls since 1967, investigations since 1972. Bronze keeps only the latest snapshot and S3 keeps the history.

The `ETag` header on each zip equals the zip's MD5, so ingestion uses it both to detect changes and to verify the download.

## Sizes and row counts

| Dataset | Zip | Unzipped | Rows |
|---|---|---|---|
| Complaints | 375 MB | 1.61 GB | 2,257,425 |
| Recalls post-2010 | 15 MB | 312 MB | 246,109 |
| Recalls pre-2010 | 7.5 MB | 84 MB | 81,710 |
| Investigations | 4.3 MB | 391 MB | 154,439 |

Snowflake COPY can't read zip files, so ingestion re-compresses each one to `.txt.gz` before uploading.

## Grain

Complaints have one row per complaint and component. `CMPLID` is unique per row, but NHTSA's spec says it is updateable and can change between files, so silver should not use it as a stable key. `ODINO` is the complaint number: there are 1,626,788 distinct values. For complaints received before 15 Dec 2002 one `ODINO` can also cover several products. 16,877 `ODINO`s have different `CDESCR` text across their rows, and the silver `complaints` model needs a rule for those.

Recalls have one row per campaign, make, model, model year and component. `RECORD_ID` is unique. The post-2010 file has 15,319 distinct campaigns (`CAMPNO`).

Investigations have one row per investigation and vehicle: 5,351 distinct investigations.

## Things silver has to deal with

- Since the May 2021 system change, blank Y/N fields show as `N` and blank numbers as `0`. For crash, fire, injured and deaths, "no" and "unknown" can't be told apart.
- `YEARTXT = 9999` means the model year is unknown or doesn't apply.
- `CDESCR` maxes out at exactly 2,048 characters, its documented width, so long complaints are cut off.
- Personal fields: `VEHICLE_OPERATOR` (an operator's name, added April 2026, empty in every row so far), `CITY`, `VIN`, and the dealer contact fields. Silver drops `VEHICLE_OPERATOR` and the dealer phone number. None of these fields go into gold or into the text sent to the RAG model.
- Model names: the flat file stores Tesla's Model Y as `MODEL Y` (7,741 rows) plus one `ROBOTAXI (MODEL Y)`. `MODEL Y (All Variants)`, which made the API search return nothing, is the API's label and isn't in the flat file. So the mapping in `dim_vehicle` has two jobs: grouping variants within the flat file, and translating to API names where the app links out to NHTSA.

## Embedding volume

Complaints filed from 2020 onward (`LDATE >= 20200101`): 429,456 distinct `ODINO`s. Their summaries average 600 characters, about 64 million tokens in total. Check Snowflake's current credit rate for `snowflake-arctic-embed-l-v2.0` before the first full embedding run, because Cortex charges aren't covered by the warehouse resource monitors.
