from pathlib import Path
import logging

import pandas as pd


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

RAW_DIR = Path("data/raw")
METADATA_PATH = RAW_DIR / "parcel_metadata.csv"
READINGS_PATH = RAW_DIR / "parcel_readings.csv"
DEFAULT_CLEANED_OUTPUT = Path("cleaned_parcel_timeseries.csv")
DEFAULT_ANALYSIS_OUTPUT = Path("crop_ndvi_summary.csv")
DEFAULT_AUDIT_OUTPUT = Path("data_quality_audit.csv")


def parse_date_column(series: pd.Series) -> pd.Series:
    """Parse ISO dates as YYYY-MM-DD and slash dates as DD/MM/YYYY."""
    values = series.astype("string").str.strip()
    parsed = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")

    # The source has both ISO dates and Indian-style slash dates, so parsing them
    # separately avoids treating 2026-02-10 as 2 October by mistake.
    iso_mask = values.str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)
    slash_mask = values.str.match(r"^\d{1,2}/\d{1,2}/\d{4}$", na=False)
    fallback_mask = ~(iso_mask | slash_mask)

    parsed.loc[iso_mask] = pd.to_datetime(values.loc[iso_mask], format="%Y-%m-%d", errors="coerce")
    parsed.loc[slash_mask] = pd.to_datetime(values.loc[slash_mask], format="%d/%m/%Y", errors="coerce")
    parsed.loc[fallback_mask] = pd.to_datetime(values.loc[fallback_mask], errors="coerce")

    return parsed.dt.normalize()


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    logging.info("Reading source CSV files")
    metadata = pd.read_csv(METADATA_PATH)
    readings = pd.read_csv(READINGS_PATH)
    return metadata, readings


def clean_metadata(metadata: pd.DataFrame) -> pd.DataFrame:
    cleaned = metadata.copy()

    # Keep join keys and categorical fields consistent before any merge.
    for column in ["parcel_id", "mill_id", "crop_type"]:
        cleaned[column] = cleaned[column].astype("string").str.strip()

    cleaned["crop_type"] = cleaned["crop_type"].str.lower()
    cleaned["sowing_date"] = parse_date_column(cleaned["sowing_date"])
    cleaned["area_hectares"] = pd.to_numeric(cleaned["area_hectares"], errors="coerce")

    cleaned = cleaned.dropna(subset=["parcel_id", "mill_id", "crop_type", "sowing_date"])
    cleaned = cleaned[cleaned["area_hectares"] > 0]
    cleaned = cleaned.drop_duplicates(subset=["parcel_id"], keep="first")

    return cleaned


def clean_readings(readings: pd.DataFrame, valid_parcel_ids: set[str]) -> pd.DataFrame:
    cleaned = readings.copy()

    cleaned["parcel_id"] = cleaned["parcel_id"].astype("string").str.strip()
    cleaned["date"] = parse_date_column(cleaned["date"])

    # Treat missing status as UNKNOWN instead of OK, because OK should only mean
    # the sensor explicitly reported a healthy reading.
    cleaned["sensor_status"] = (
        cleaned["sensor_status"]
        .astype("string")
        .str.strip()
        .str.upper()
        .fillna("UNKNOWN")
    )

    for column in ["ndvi_value", "temperature_c", "rainfall_mm"]:
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")

    # NDVI outside [-1, 1] is invalid. Keep the row for weather/status context,
    # but null the NDVI value so it cannot affect averages.
    cleaned["had_invalid_ndvi"] = ~cleaned["ndvi_value"].between(-1, 1)
    cleaned.loc[cleaned["had_invalid_ndvi"], "ndvi_value"] = pd.NA

    cleaned["had_bad_source_status"] = cleaned["sensor_status"] != "OK"

    # Rows without metadata cannot support crop-level analysis, so remove them
    # before the join and record the decision in the audit output.
    cleaned = cleaned.dropna(subset=["parcel_id", "date"])
    cleaned = cleaned[cleaned["parcel_id"].isin(valid_parcel_ids)]

    return collapse_duplicate_readings(cleaned)


def resolve_sensor_status(statuses: pd.Series) -> str:
    values = set(statuses.dropna())

    # For duplicate source rows, one healthy reading is enough to keep the
    # parcel-date usable while still preserving source quality flags separately.
    if "OK" in values:
        return "OK"
    if "ERROR" in values:
        return "ERROR"
    return "UNKNOWN"


def collapse_duplicate_readings(readings: pd.DataFrame) -> pd.DataFrame:
    # The final dataset must be one row per parcel and date. Numeric readings are
    # averaged, while status and quality flags summarize the source rows.
    grouped = (
        readings
        .groupby(["parcel_id", "date"], as_index=False)
        .agg(
            ndvi_value=("ndvi_value", "mean"),
            temperature_c=("temperature_c", "mean"),
            rainfall_mm=("rainfall_mm", "mean"),
            sensor_status=("sensor_status", resolve_sensor_status),
            source_rows=("parcel_id", "size"),
            had_bad_source_status=("had_bad_source_status", "any"),
            had_invalid_ndvi=("had_invalid_ndvi", "any"),
        )
    )

    grouped["has_duplicate_source_rows"] = grouped["source_rows"] > 1
    return grouped


def build_clean_timeseries(metadata: pd.DataFrame, readings: pd.DataFrame) -> pd.DataFrame:
    logging.info("Cleaning metadata and readings")
    cleaned_metadata = clean_metadata(metadata)
    cleaned_readings = clean_readings(readings, set(cleaned_metadata["parcel_id"]))

    logging.info("Joining readings with parcel metadata")
    joined = cleaned_readings.merge(cleaned_metadata, on="parcel_id", how="left", validate="many_to_one")

    # Keep a stable column order so reviewers and downstream queries see the same
    # layout every time the pipeline is run.
    ordered_columns = [
        "parcel_id",
        "date",
        "mill_id",
        "crop_type",
        "sowing_date",
        "area_hectares",
        "ndvi_value",
        "temperature_c",
        "rainfall_mm",
        "sensor_status",
        "source_rows",
        "has_duplicate_source_rows",
        "had_bad_source_status",
        "had_invalid_ndvi",
    ]

    return joined[ordered_columns].sort_values(["parcel_id", "date"]).reset_index(drop=True)


def analyze_crop_ndvi(cleaned: pd.DataFrame) -> pd.DataFrame:
    logging.info("Computing crop-level NDVI summary")
    analysis_rows = []

    # The brief asks us to ignore bad sensor rows for this metric, so only OK
    # readings with valid NDVI values are used in the before/after windows.
    usable = cleaned[(cleaned["sensor_status"] == "OK") & cleaned["ndvi_value"].notna()].copy()
    usable["days_from_sowing"] = (usable["date"] - usable["sowing_date"]).dt.days

    for crop_type, crop_data in usable.groupby("crop_type", sort=True):
        # Use the 30 complete days before and after sowing; day 0 is excluded so
        # the two windows do not mix the sowing day into either side.
        before = crop_data[crop_data["days_from_sowing"].between(-30, -1)]
        after = crop_data[crop_data["days_from_sowing"].between(1, 30)]
        contributing_parcels = pd.concat([before["parcel_id"], after["parcel_id"]]).nunique()

        analysis_rows.append(
            {
                "crop_type": crop_type,
                "mean_ndvi_before": round(before["ndvi_value"].mean(), 4),
                "mean_ndvi_after": round(after["ndvi_value"].mean(), 4),
                "n_parcels": int(contributing_parcels),
            }
        )

    return pd.DataFrame(analysis_rows)


def percentage(count: int, total: int) -> str:
    return f"{(count / total * 100):.1f}%" if total else "0.0%"


def build_data_quality_audit(metadata: pd.DataFrame, readings: pd.DataFrame) -> pd.DataFrame:
    reading_dates = parse_date_column(readings["date"])
    sowing_dates = parse_date_column(metadata["sowing_date"])
    normalized_status = readings["sensor_status"].astype("string").str.strip().str.upper()
    duplicate_rows = readings.assign(parsed_date=reading_dates).duplicated(["parcel_id", "parsed_date"], keep=False)
    unknown_parcels = ~readings["parcel_id"].isin(metadata["parcel_id"])
    invalid_ndvi = ~readings["ndvi_value"].between(-1, 1)

    total_readings = len(readings)
    total_metadata = len(metadata)

    rows = [
        {
            "issue": "Mixed reading date formats",
            "count": int(readings["date"].astype(str).str.contains("/").sum()),
            "percent": percentage(int(readings["date"].astype(str).str.contains("/").sum()), total_readings),
            "decision": "repair",
        },
        {
            "issue": "Invalid reading dates after parsing",
            "count": int(reading_dates.isna().sum()),
            "percent": percentage(int(reading_dates.isna().sum()), total_readings),
            "decision": "drop if present",
        },
        {
            "issue": "Invalid sowing dates after parsing",
            "count": int(sowing_dates.isna().sum()),
            "percent": percentage(int(sowing_dates.isna().sum()), total_metadata),
            "decision": "drop if present",
        },
        {
            "issue": "Missing sensor status",
            "count": int(readings["sensor_status"].isna().sum()),
            "percent": percentage(int(readings["sensor_status"].isna().sum()), total_readings),
            "decision": "flag as UNKNOWN",
        },
        {
            "issue": "Bad sensor status after normalization",
            "count": int((normalized_status == "ERROR").sum()),
            "percent": percentage(int((normalized_status == "ERROR").sum()), total_readings),
            "decision": "flag and exclude from NDVI analysis",
        },
        {
            "issue": "NDVI outside valid range [-1, 1]",
            "count": int(invalid_ndvi.sum()),
            "percent": percentage(int(invalid_ndvi.sum()), total_readings),
            "decision": "set NDVI to null and flag",
        },
        {
            "issue": "Duplicate parcel-date source rows",
            "count": int(duplicate_rows.sum()),
            "percent": percentage(int(duplicate_rows.sum()), total_readings),
            "decision": "collapse to one parcel-date row",
        },
        {
            "issue": "Readings with parcel_id missing from metadata",
            "count": int(unknown_parcels.sum()),
            "percent": percentage(int(unknown_parcels.sum()), total_readings),
            "decision": "drop before join",
        },
        {
            "issue": "Metadata parcels without readings",
            "count": len(set(metadata["parcel_id"]) - set(readings["parcel_id"])),
            "percent": percentage(len(set(metadata["parcel_id"]) - set(readings["parcel_id"])), total_metadata),
            "decision": "document in audit",
        },
    ]

    return pd.DataFrame(rows)


def run_pipeline() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    metadata, readings = load_inputs()
    audit = build_data_quality_audit(metadata, readings)
    cleaned = build_clean_timeseries(metadata, readings)
    analysis = analyze_crop_ndvi(cleaned)

    logging.info("Writing pipeline outputs")
    audit.to_csv(DEFAULT_AUDIT_OUTPUT, index=False)
    cleaned.to_csv(DEFAULT_CLEANED_OUTPUT, index=False)
    analysis.to_csv(DEFAULT_ANALYSIS_OUTPUT, index=False)

    return cleaned, analysis, audit


def main() -> None:
    cleaned, analysis, audit = run_pipeline()

    print(f"Wrote data quality audit to {DEFAULT_AUDIT_OUTPUT}")
    print(f"Wrote {len(cleaned):,} cleaned parcel-date rows to {DEFAULT_CLEANED_OUTPUT}")
    print(f"Wrote crop NDVI summary to {DEFAULT_ANALYSIS_OUTPUT}")
    print(audit.to_string(index=False))
    print(analysis.to_string(index=False))


if __name__ == "__main__":
    main()
