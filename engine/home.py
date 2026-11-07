"""Home page data: 'Supply operations: what needs attention', built ONLY from the synthetic data pack.

Same JSON shape as the old shop-floor endpoint, so the page needs only text changes. No invented numbers
and no invented thresholds: status levels come from the pack's own `risk_status` (green/amber/red per
product x market, sheet Supply_Operations); values and trends come from Product_Market_Monthly.
"""
from __future__ import annotations

from datetime import datetime

from .estate import load

LEVEL = {"green": 0, "amber": 1, "red": 2}
TREND_MONTHS = 7


def home_data() -> dict:
    d = load()
    pm, so = d["Product_Market_Monthly"], d["Supply_Operations"]
    # All-zero rows in the pack = product not sold in that market. Ignore them (they would drag averages down).
    pm = pm[pm.net_sales_m_eur_syn > 0]
    active = pm[["product_id", "market_id"]].drop_duplicates()
    so = so.merge(active, on=["product_id", "market_id"])
    prod = d["Products"].set_index("product_id").product_name
    mk = d["Markets"].set_index("market_id").market_name
    months = sorted(pm.month.unique())
    last, prev = months[-1], months[-2]

    def month_mean(col, m):
        return float(pm[pm.month == m][col].mean())

    def month_sum(col, m):
        return float(pm[pm.month == m][col].sum())

    risk = so.assign(level=so.risk_status.map(LEVEL))
    red_n = int((risk.level == 2).sum())
    tiles = [
        {"kpi": "On-time in full", "unit": "%", "direction": "higher_is_better", "level": 0, "status_basis": "none",
         "value": round(month_mean("otif_pct_syn", last), 1),
         "delta": round(month_mean("otif_pct_syn", last) - month_mean("otif_pct_syn", prev), 1)},
        {"kpi": "Inventory coverage", "unit": "weeks", "direction": "higher_is_better", "level": 0, "status_basis": "none",
         "value": round(month_mean("inventory_weeks_syn", last), 1),
         "delta": round(month_mean("inventory_weeks_syn", last) - month_mean("inventory_weeks_syn", prev), 1)},
        {"kpi": "Units shipped", "unit": "units", "direction": "higher_is_better", "level": 0, "status_basis": "none",
         "value": round(month_sum("units_shipped_syn", last)),
         "delta": round(month_sum("units_shipped_syn", last) - month_sum("units_shipped_syn", prev))},
        {"kpi": "Routes at red risk", "unit": f"of {len(risk)}", "direction": "lower_is_better",
         "level": 2 if red_n else 0, "status_basis": "pack risk_status", "value": red_n, "delta": 0},
    ]

    # one pill per product, from the pack's risk_status. Almost every product has some red route, so "worst market"
    # says nothing: compare the product's red share with the portfolio's own red share instead (no invented threshold).
    portfolio_red = red_n / len(risk)
    lines = []
    for pid, g in risk.groupby("product_id"):
        share = (g.level == 2).mean()
        lvl = 2 if share > portfolio_red else (1 if (g.level == 2).any() else 0)
        lines.append({"line": prod[pid], "level": lvl,
                      "product": f"{share:.0%} of routes red vs {portfolio_red:.0%} portfolio-wide"})
    lines.sort(key=lambda x: (-x["level"], x["line"]))

    # attention cards: the red routes with the lowest OTIF this month
    target = round(month_mean("otif_pct_syn", last), 1)   # portfolio average this month, from the pack
    reds = risk[risk.level == 2].merge(pm[pm.month == last][["product_id", "market_id", "otif_pct_syn"]].rename(columns={"otif_pct_syn": "otif_now"}),
                                       on=["product_id", "market_id"]).sort_values("otif_now")
    items = []
    for r in reds.head(4).itertuples():
        s = pm[(pm.product_id == r.product_id) & (pm.market_id == r.market_id)].sort_values("month")
        trend = [{"date": m.strftime("%Y-%m-%d"), "value": round(float(v), 1)}
                 for m, v in zip(s.month.tail(TREND_MONTHS), s.otif_pct_syn.tail(TREND_MONTHS))]
        older = s[s.month < last].otif_pct_syn
        items.append({"line": f"{prod[r.product_id]} · {mk[r.market_id]}", "kpi": "On-time in full", "unit": "%",
                      "value": round(float(r.otif_now), 1), "level": 2, "direction": "higher_is_better",
                      "baseline": round(float(older.mean()), 1), "target": target, "trend": trend,
                      "supply_source": r.supply_source, "risk_status": r.risk_status})

    return {
        "name": "Supply operations: what needs attention",
        "audience": "Supply chain and operations leads",
        "date": last.strftime("%Y-%m-%d"),
        "updated": datetime.now().isoformat(timespec="seconds"),
        "tiles": tiles, "lines": lines, "items": items,
        "attention_count": red_n, "all_clear": red_n == 0,
        # labels so the page does not have to say 'yesterday' or 'last 7 days' for monthly data
        "labels": {"period": last.strftime("%B %Y"), "delta": "vs previous month",
                   "trend": f"Last {TREND_MONTHS} months", "entity": "Route", "target": "portfolio average",
                   "source": "Synthetic Bayer data pack (Product_Market_Monthly, Supply_Operations). Nothing is invented."},
    }
