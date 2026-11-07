"""Supply risk from the data pack: which product-market routes are red, and the worst of them.

Same logic as the home page (engine/home.py), parameterised by scope, so the dashboard builder can make the same
numbers from a plain-language request. Routes the product is not sold on (all-zero rows in the pack) are ignored.
Nothing here is invented: the risk label is the pack's own `Supply_Operations.risk_status`.
"""
from __future__ import annotations

from .estate import load

TREND_MONTHS = 7
WORST_N = 4


def _scope(products: list[str], markets: list[str]):
    d = load()
    pm = d["Product_Market_Monthly"]
    pm = pm[pm.net_sales_m_eur_syn > 0]   # all-zero rows = product not sold in that market
    ids = d["Products"].set_index("product_name").product_id
    if products and "All Pharma" not in products:
        pm = pm[pm.product_id.isin([ids[p] for p in products if p in ids.index])]
    if markets:
        pm = pm[pm.market_id.isin(markets)]
    active = pm[["product_id", "market_id"]].drop_duplicates()
    so = d["Supply_Operations"].merge(active, on=["product_id", "market_id"])
    return pm, so


def supply_risk(products: list[str], markets: list[str]) -> dict | None:
    """{red, total, breakdown[], worst[], target, ...} for the requested scope, or None if no route is in scope."""
    d = load()
    pm, so = _scope(products, markets)
    if so.empty:
        return None
    pname = d["Products"].set_index("product_id").product_name
    mname = d["Markets"].set_index("market_id").market_name
    red = so[so.risk_status == "red"]

    # breakdown: by product when several products are in scope (or all), by market when one product is asked for
    by_product = so.product_id.nunique() > 1
    key, names = ("product_id", pname) if by_product else ("market_id", mname)
    breakdown = []
    for k, g in so.groupby(key):
        r = int((g.risk_status == "red").sum())
        breakdown.append({"label": names[k], "red": r, "total": int(len(g)), "share_pct": round(100 * r / len(g), 1)})
    breakdown.sort(key=lambda b: (-b["share_pct"], b["label"]))

    last = pm.month.max()
    now = pm[pm.month == last][["product_id", "market_id", "otif_pct_syn"]].rename(columns={"otif_pct_syn": "otif_now"})
    worst = []
    for r in red.merge(now, on=["product_id", "market_id"]).sort_values("otif_now").head(WORST_N).itertuples():
        s = pm[(pm.product_id == r.product_id) & (pm.market_id == r.market_id)].sort_values("month").tail(TREND_MONTHS)
        worst.append({"route": f"{pname[r.product_id]} · {mname[r.market_id]}", "supply_source": r.supply_source,
                      "series": [(m.strftime("%b %y"), round(float(v), 1)) for m, v in zip(s.month, s.otif_pct_syn)]})
    return {"red": int(len(red)), "total": int(len(so)), "breakdown": breakdown, "by": "product" if by_product else "market",
            "worst": worst, "target": round(float(pm[pm.month == last].otif_pct_syn.mean()), 1),
            "month": last.strftime("%B %Y"), "scope_markets": int(so.market_id.nunique())}
