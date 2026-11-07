"""Load the synthetic Bayer BI estate (data pack xlsx) into pandas DataFrames.

All values in the pack are SYNTHETIC. Excel stores dates as serial numbers,
so date-like columns are converted to real datetimes here.
"""
from functools import lru_cache
from pathlib import Path

import pandas as pd

PACK = Path(__file__).parent / "data" / "bayer_pack.xlsx"

# Sheets that are documentation, not tables.
SKIP = {"START_HERE", "Package_Overview", "Change_Log", "Public_References"}
DATE_COLS = {"created_date", "last_modified", "month", "request_date", "milestone_date", "date"}


def _fix_dates(df: pd.DataFrame) -> pd.DataFrame:
    for col in DATE_COLS & set(df.columns):
        if pd.api.types.is_numeric_dtype(df[col]):
            df[col] = pd.to_datetime(df[col], unit="D", origin="1899-12-30")
    return df


@lru_cache(maxsize=1)
def load() -> dict[str, pd.DataFrame]:
    """Return {sheet_name: DataFrame}. Cached, so call freely."""
    sheets = pd.read_excel(PACK, sheet_name=None)
    return {n: _fix_dates(df) for n, df in sheets.items() if n not in SKIP}


if __name__ == "__main__":
    for name, df in load().items():
        print(f"{name:24s} {df.shape[0]:5d} rows  cols={list(df.columns)[:6]}...")
