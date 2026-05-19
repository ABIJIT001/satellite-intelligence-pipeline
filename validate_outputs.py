from pathlib import Path

import pandas as pd


CLEANED_PATH = Path("cleaned_parcel_timeseries.csv")
SUMMARY_PATH = Path("crop_ndvi_summary.csv")
AUDIT_PATH = Path("data_quality_audit.csv")


def main() -> None:
    cleaned = pd.read_csv(CLEANED_PATH)
    summary = pd.read_csv(SUMMARY_PATH)
    audit = pd.read_csv(AUDIT_PATH)

    required_cleaned_columns = {
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
    }
    missing_columns = required_cleaned_columns - set(cleaned.columns)
    assert not missing_columns, f"Missing columns in cleaned output: {sorted(missing_columns)}"

    duplicate_count = cleaned.duplicated(["parcel_id", "date"]).sum()
    assert duplicate_count == 0, f"Found {duplicate_count} duplicate parcel-date rows"

    valid_statuses = {"OK", "ERROR", "UNKNOWN"}
    unexpected_statuses = set(cleaned["sensor_status"].dropna()) - valid_statuses
    assert not unexpected_statuses, f"Unexpected sensor statuses: {sorted(unexpected_statuses)}"

    ndvi = cleaned["ndvi_value"].dropna()
    assert ndvi.between(-1, 1).all(), "Cleaned NDVI contains values outside [-1, 1]"

    required_summary_columns = {"crop_type", "mean_ndvi_before", "mean_ndvi_after", "n_parcels"}
    missing_summary_columns = required_summary_columns - set(summary.columns)
    assert not missing_summary_columns, f"Missing columns in summary output: {sorted(missing_summary_columns)}"

    required_audit_columns = {"issue", "count", "percent", "decision"}
    missing_audit_columns = required_audit_columns - set(audit.columns)
    assert not missing_audit_columns, f"Missing columns in audit output: {sorted(missing_audit_columns)}"

    print("Validation passed")
    print(f"Cleaned rows: {len(cleaned):,}")
    print(f"Summary rows: {len(summary):,}")
    print(f"Audit rows: {len(audit):,}")


if __name__ == "__main__":
    main()
