"""Measures that are NOT governed KPIs but for which the data pack holds data (BI usage and forecast value).

A request for one of these is a NEW_BUILD (no governed KPI exists: define it first), yet a first working version can be
built straight away from the pack, marked 'not governed'. Anything not listed here has no data in the pack at all.
"""
from __future__ import annotations

# (keywords found in a request or measure, label, sheet, column, aggregation, unit, multiplier)
UNGOVERNED = [
    (("forecast value", "sales forecast", "forecast sales", "planned sales"), "Forecast (net sales)", "pmm", "forecast_m_eur_syn", "sum", "EUR", 1e6),
    (("view", "how often", "opened", "traffic"), "Report views", "usage", "views", "sum", "count", 1),
    (("export",), "Report exports", "usage", "exports", "sum", "count", 1),
    (("subscri",), "Report subscriptions", "usage", "subscriptions", "sum", "count", 1),
    (("error", "failing", "failure", "broken", "fail"), "Report query errors", "usage", "query_errors", "sum", "count", 1),
    (("load time", "loading", "slow", "latency", "report speed", "report performance"), "Report load time", "usage", "avg_load_ms", "mean", "ms", 1),
    # supply risk is a snapshot per product-market route (green/amber/red), not a monthly series: see engine/supply.py
    (("supply risk", "supply-risk", "red risk", "risk status", "routes at risk", "at red risk", "red route", "risky route", "route risk"),
     "Routes at red risk", "supply", None, None, "routes", 1),
]
PLATFORMS = {"power bi": "Power BI", "powerbi": "Power BI", "tableau": "Tableau"}


def ungoverned_label(text: str) -> str | None:
    """The label of the first non-governed measure the text is about, if the pack has data for it."""
    t = text.lower()
    for keys, label, *_ in UNGOVERNED:
        if any(k in t for k in keys):
            return label
    return None


def find_ungoverned(text: str) -> list[str]:
    """All non-governed measures a request mentions (labels, in list order, no duplicates)."""
    t = text.lower()
    return [label for keys, label, *_ in UNGOVERNED if any(k in t for k in keys)]


def find_platforms(text: str) -> list[str]:
    t = text.lower()
    return list(dict.fromkeys(v for k, v in PLATFORMS.items() if k in t))
