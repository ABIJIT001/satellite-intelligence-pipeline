# Satellite Intelligence Data Pipeline

## Overview

This repository contains a small data pipeline for the satellite intelligence case study. It ingests parcel metadata and daily parcel readings, audits the source data, cleans the issues that affect downstream analysis, joins the two files into one parcel-date time series, and produces the requested crop-level NDVI summary.

## Repository Contents

- `pipeline.py` - end-to-end cleaning and analysis pipeline
- `data/raw/parcel_metadata.csv` - source parcel metadata
- `data/raw/parcel_readings.csv` - source daily readings
- `data_quality_audit.csv` - generated audit metrics from the raw files
- `cleaned_parcel_timeseries.csv` - cleaned joined output, one row per `parcel_id` x `date`
- `crop_ndvi_summary.csv` - requested before/after sowing NDVI summary
- `validate_outputs.py` - lightweight checks for the generated outputs
- `requirements.txt` - Python dependency list

## How To Run

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python pipeline.py
python validate_outputs.py
```

Expected output:

```text
Wrote data quality audit to data_quality_audit.csv
Wrote 3,399 cleaned parcel-date rows to cleaned_parcel_timeseries.csv
Wrote crop NDVI summary to crop_ndvi_summary.csv
```

## Data Quality Audit

Raw input sizes:

- `parcel_metadata.csv`: 28 rows
- `parcel_readings.csv`: 3,447 rows

| Issue | Prevalence | Decision | Justification |
| --- | ---: | --- | --- |
| Mixed reading date formats (`YYYY-MM-DD` and `DD/MM/YYYY`) | 686 slash-formatted dates, 19.9% of readings | Repair | All dates parsed cleanly, so I normalized them to a real datetime column before joining or deduplicating. |
| Inconsistent `sensor_status` casing and whitespace (`OK`, `ok`, ` OK`, `OK `, `Error`, `ERROR`, `error`) | 8 raw variants | Repair | Status values represent the same small set of states; trimming and uppercasing avoids false categories. |
| Missing `sensor_status` | 137 rows, 4.0% of readings | Flag | A missing sensor state is not safely equivalent to OK, so it is retained as `UNKNOWN` and excluded from analysis. |
| Bad sensor statuses | 246 rows, 7.1% of readings after normalization | Flag | The row can still be useful for operational auditing, but NDVI analysis ignores non-OK sensor rows. |
| NDVI outside valid range `[-1, 1]` | 104 rows, 3.0% of readings | Repair + flag | Invalid NDVI values are set to null and flagged with `had_invalid_ndvi` instead of dropping the whole weather reading. |
| Duplicate readings after date normalization | 8 parcel-date pairs, 16 source rows | Repair | The required output is one row per parcel-date, so duplicates are collapsed by averaging numeric fields and resolving status. |
| Readings for parcel IDs missing from metadata (`PARCEL_098`, `PARCEL_099`) | 40 rows, 1.2% of readings | Drop | These cannot be joined to crop, mill, sowing date, or area, so they are not valid for the joined time series. |
| Metadata parcels without readings (`PARCEL_050`, `PARCEL_051`, `PARCEL_052`) | 3 parcels, 10.7% of metadata | Flag in audit | They have no daily observations, so they naturally do not appear in the time-series output. |
| Missing metadata fields | 0 rows | No action | Parcel metadata was complete for the rows provided. |
| Invalid or non-positive area | 0 rows | No action | All parcel areas were positive. |
| Invalid reading or sowing dates | 0 rows | No action | All dates were parseable after allowing mixed formats. |

## Cleaning Approach

The pipeline keeps the logic practical and reviewable:

1. Load both CSVs with pandas.
2. Generate `data_quality_audit.csv` from the raw inputs.
3. Strip identifier fields and normalize `crop_type` and `sensor_status`.
4. Parse mixed date formats into consistent datetime values.
5. Mark invalid NDVI values as null while keeping a quality flag.
6. Remove readings whose `parcel_id` does not exist in metadata.
7. Collapse duplicate `parcel_id` and `date` readings into a single row.
8. Join readings to metadata with a many-to-one merge.
9. Write `cleaned_parcel_timeseries.csv`.
10. Compute and write `crop_ndvi_summary.csv`.

Duplicate resolution:

- Numeric columns are averaged.
- `sensor_status` is set to `OK` if at least one duplicate source row is OK, otherwise `ERROR` if any source row is an error, otherwise `UNKNOWN`.
- `source_rows`, `has_duplicate_source_rows`, `had_bad_source_status`, and `had_invalid_ndvi` are retained so the cleanup remains auditable.

## Analysis Output

Rows with bad or unknown sensor status are ignored for this analysis. The before window is days `-30` to `-1` from sowing date, and the after window is days `1` to `30`.

| crop_type | mean_ndvi_before | mean_ndvi_after | n_parcels |
| --- | ---: | ---: | ---: |
| soybean | 0.1706 | 0.3126 | 4 |
| sugarcane | 0.1775 | 0.3361 | 19 |
| wheat | 0.1761 | 0.3101 | 2 |

The mean NDVI increases after sowing for all three crop types, which is directionally consistent with vegetation becoming more active after planting. Sugarcane has the highest post-sowing NDVI in this sample, while soybean shows the largest before-to-after lift. The wheat result should be treated carefully because it is based on only two contributing parcels.

## Production-Readiness Reflection

If this pipeline ran daily and the dataset was 100x larger, I would change three things:

1. Move from a single pandas script to an orchestrated job with clear raw, cleaned, and analytics layers.
2. Add automated data quality checks for schema, date ranges, duplicate rates, valid NDVI range, and join coverage.
3. Partition outputs by date and write to a columnar format such as Parquet for faster downstream queries.

I would monitor:

- Row counts by day and parcel.
- Percentage of bad or unknown sensor statuses.
- NDVI out-of-range rate.
- Number of readings that fail to join to metadata.
- Duplicate parcel-date rate after date parsing.
- Freshness of incoming readings.

The most likely silent break is a source-system format change, especially dates or sensor statuses. For example, a new date format might still parse but be interpreted incorrectly, or a new status like `WARN` could be treated as bad/unknown without anyone noticing unless monitored.

## AI Tool Usage

I used AI assistance to help structure the pipeline, identify edge cases to audit, and draft the README. I validated the outputs locally by running the pipeline and checking the cleaned row count, uniqueness of `parcel_id` x `date`, and the generated analysis table.

## Loom Walkthrough Checklist

The brief says the Loom video is mandatory. In the walkthrough, I would cover:

1. Show the repository structure and run `python pipeline.py`.
2. Explain the three most important cleaning choices: status normalization, invalid NDVI handling, and duplicate parcel-date collapse.
3. Show `cleaned_parcel_timeseries.csv` and `crop_ndvi_summary.csv`.
4. Verbally answer the production-readiness reflection.

## Rebuilding From Scratch

From an empty folder:

```bash
mkdir satellite_intelligence_pipeline
cd satellite_intelligence_pipeline
mkdir data\raw
copy C:\Users\DELL\Downloads\parcel_metadata.csv data\raw\parcel_metadata.csv
copy C:\Users\DELL\Downloads\parcel_readings.csv data\raw\parcel_readings.csv
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install pandas
```

Then create `pipeline.py`, add the cleaning functions, and run:

```bash
python pipeline.py
```

The two files to verify are:

```text
data_quality_audit.csv
cleaned_parcel_timeseries.csv
crop_ndvi_summary.csv
```

## Requirement Checklist

- Code/script included: `pipeline.py`
- Cleaned output included: `cleaned_parcel_timeseries.csv`
- Data quality audit included: README table and `data_quality_audit.csv`
- Approach included: `Cleaning Approach` section
- Analysis output included: `Analysis Output` section and `crop_ndvi_summary.csv`
- Production reflection included: `Production-Readiness Reflection` section
- Loom requirement acknowledged: `Loom Walkthrough Checklist` section
