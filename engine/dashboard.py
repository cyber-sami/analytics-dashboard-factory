"""Turn an APPROVED spec into a dashboard definition.

Gate: nothing is generated until a human has approved the proposal (governance.decide).

Data rule: ONLY values from the (synthetic) data pack. Nothing is invented. Every tile is one of:
  - governed KPI with pack data      -> chart, `governed: true`
  - not a governed KPI, pack has data -> chart, `governed: false` ("data exists, no governed definition yet")
  - governed KPI, no pack data        -> `state: "pending"`, reason `no_pack_data`
  - not a KPI and no pack data        -> `state: "pending"`, reason `not_in_pack` (e.g. HR attrition)
Pending tiles carry `headline` and `message` so the UI can say exactly why there is no chart.
"""
from __future__ import annotations

import pandas as pd

from . import governance as gov
from . import lifecycle
from .estate import load
from .measures import UNGOVERNED, direction, ungoverned_label
from .supply import supply_risk
from .recommend import _features

# ---- governed KPIs: canonical metric -> how to compute a monthly series from the pack
# (column, aggregation, multiplier, unit, how it was derived)
PMM = {
    "Net Sales": ("net_sales_m_eur_syn", "sum", 1e6, "EUR",
                  "net sales, summed over scope (the pack mixes actual-like and forecast-like synthetic months)"),
    "Units Shipped": ("units_shipped_syn", "sum", 1, "units", "units shipped, summed over scope"),
    "Market Access Coverage": ("market_access_coverage_pct_syn", "mean", 1, "%", "market access coverage, averaged over scope"),
    "On-Time In-Full": ("otif_pct_syn", "mean", 1, "%", "OTIF, averaged over scope"),
    "Inventory Coverage": ("inventory_weeks_syn", "mean", 1, "weeks", "inventory weeks, averaged over scope"),
}

def _scope(products: list[str], markets: list[str]):
    d = load()
    ids = d["Products"].set_index("product_name").product_id
    pids = None if (not products or "All Pharma" in products) else [ids[p] for p in products if p in ids.index]
    return pids, (markets or None)


def _pmm(col, agg, mult, products, markets):
    pids, mk = _scope(products, markets)
    pm = load()["Product_Market_Monthly"]
    if pids is not None:
        pm = pm[pm.product_id.isin(pids)]
    if mk:
        pm = pm[pm.market_id.isin(mk)]
    # The pack holds all-zero rows for product-market pairs that are not sold (not launched). Averaging those in
    # would drag every % and weeks figure down, so only active pairs count.
    pm = pm[pm.net_sales_m_eur_syn > 0]
    return None if pm.empty else pm.groupby("month")[col].agg(agg).sort_index() * mult, pm


def _usage(col, agg, products, platforms=()):
    """Asset_Usage has no market, so it is scoped by the products of the assets (All Pharma = everything) and,
    when asked, by BI platform (Power BI / Tableau)."""
    d = load()
    a = d["Analytics_Assets"]
    if products and "All Pharma" not in products:
        a = a[a.product_scope.isin(products)]
    if platforms:
        a = a[a.platform.isin(platforms)]
    u = d["Asset_Usage"]
    u = u[u.asset_id.isin(a.asset_id)]
    return None if u.empty else u.groupby("month")[col].agg(agg).sort_index()


def _points(s: pd.Series):
    return [(m.strftime("%b %y"), round(float(v), 2)) for m, v in s.items()]


def _scope_txt(products, markets, n_markets):
    return f"{', '.join(products) or 'all products'} · {len(markets) or n_markets} market(s)"


def governed_series(metric: str, products: list[str], markets: list[str]):
    """(unit, points, note) for a governed KPI that the pack can support, else None. Derived series say so."""
    if metric in PMM:
        col, agg, mult, unit, how = PMM[metric]
        s, pm = _pmm(col, agg, mult, products, markets)
        if s is None:
            return None
        return unit, _points(s), f"{how} · {_scope_txt(products, markets, pm.market_id.nunique())}"
    if metric == "Forecast Variance":   # catalog unit is EUR: net sales minus forecast, both from the pack
        a, pm = _pmm("net_sales_m_eur_syn", "sum", 1e6, products, markets)
        f, _ = _pmm("forecast_m_eur_syn", "sum", 1e6, products, markets)
        if a is None:
            return None
        return "EUR", _points(a - f), f"derived: net sales minus forecast, summed · {_scope_txt(products, markets, pm.market_id.nunique())}"
    if metric == "Net Sales Growth":
        s, pm = _pmm("net_sales_m_eur_syn", "sum", 1, products, markets)
        if s is None or len(s) < 2:
            return None
        g = (s.pct_change() * 100).dropna()
        return "%", _points(g), f"derived: month-on-month change of net sales · {_scope_txt(products, markets, pm.market_id.nunique())}"
    if metric == "Demand Forecast Accuracy":
        pids, mk = _scope(products, markets)
        s, pm = _pmm("forecast_variance_pct", "mean", 1, products, markets)
        if s is None:
            return None
        acc = pm.assign(a=100 - pm.forecast_variance_pct.abs()).groupby("month").a.mean().sort_index()
        return "%", _points(acc), f"derived: 100 − |forecast variance %|, averaged · {_scope_txt(products, markets, pm.market_id.nunique())}"
    if metric == "Demand Forecast Bias":
        a, pm = _pmm("net_sales_m_eur_syn", "sum", 1, products, markets)
        f, _ = _pmm("forecast_m_eur_syn", "sum", 1, products, markets)
        if a is None:
            return None
        return "%", _points((a - f) / f * 100), f"derived: (actual − forecast) / forecast, summed over scope · {_scope_txt(products, markets, pm.market_id.nunique())}"
    if metric == "Report Adoption":
        s = _usage("unique_users", "sum", products)
        if s is None:
            return None
        return "count", _points(s), f"monthly unique report users, summed over {', '.join(products) or 'all'} assets"
    return None


def ungoverned_series(measure: str, products: list[str], markets: list[str], platforms: list[str] = ()):
    """(label, unit, points, note) when the pack holds data for a measure that is NOT a governed KPI."""
    m = measure.lower()
    for keys, label, sheet, col, agg, unit, mult in UNGOVERNED:
        if sheet == "supply":
            continue   # a snapshot per route, not a monthly series: built by supply_tiles()
        if any(k in m for k in keys):
            if sheet == "pmm":
                s, pm = _pmm(col, agg, mult, products, markets)
                note = _scope_txt(products, markets, pm.market_id.nunique()) if s is not None else ""
            else:
                s = _usage(col, agg, products, platforms)
                who = f"{', '.join(platforms) + ' ' if platforms else ''}{', '.join(products) or 'all'} assets"
                note = f"summed over {who}" if agg == "sum" else f"averaged over {who}"
            if s is not None:
                return label, unit, _points(s), f"{label.lower()} from the data pack · {note}"
    return None


# measures that can swing around zero: a % change versus last month is meaningless for them
SIGNED = {"Forecast Variance", "Net Sales Growth", "Demand Forecast Bias"}


def _ready(metric, kpi_id, unit, pts, note, governed, origin, extra=None):
    vals = [v for _, v in pts]
    signed = metric in SIGNED or any(v <= 0 for v in vals)
    return {"metric": metric, "state": "ready", "kpi_id": kpi_id, "unit": unit, "governed": governed,
            "data_source": "pack", "data_note": note, "origin": origin,
            "frequency": "monthly", "criticality": "high" if governed else "not rated",
            "value": vals[-1], "previous": vals[-2] if len(vals) > 1 else vals[-1],
            "change_basis": "absolute" if signed else "percent",   # UI: show the absolute change when 'absolute'
            "direction": direction(metric),   # higher_is_better | lower_is_better | None (neutral): decides green/red
            "series": [{"month": m, "value": v} for m, v in pts], **(extra or {})}


MAX_TILES = 18


def _slices(products: list[str], markets: list[str], n_kpis: int):
    """What to show side by side. Several products asked for: one tile per product (the request is a comparison).
    One product and 2-6 markets: one tile per market. Otherwise one aggregated tile. Never aggregate a comparison
    into a single number."""
    d = load()
    if len(products) >= 2 and "All Pharma" not in products and len(products) * n_kpis <= MAX_TILES:
        return [(p, [p], markets) for p in products]
    if 2 <= len(markets) <= 6 and len(markets) * n_kpis <= MAX_TILES:
        names = d["Markets"].set_index("market_id").market_name
        return [(names.get(m, m), products, [m]) for m in markets]
    return [(None, products, markets)]


def supply_tiles(products: list[str], markets: list[str]) -> list[dict]:
    """Supply risk from the pack's own risk label: a count tile with a breakdown, then the worst red routes with their
    on-time-in-full trend against the average of the routes in scope. Same numbers as the home page."""
    r = supply_risk(products, markets)
    where = f"{', '.join(products) or 'all products'} · {', '.join(markets) or 'all markets'}"
    if not r:
        return [{"metric": "Routes at red risk", "state": "pending", "reason": "not_in_pack", "origin": "to be defined",
                 "headline": "No routes in scope", "message": f"The data pack has no sold product-market routes for {where}."}]
    tiles = [{
        "metric": "Routes at red risk", "state": "ready", "kind": "snapshot", "kpi_id": "not a governed KPI", "unit": "routes",
        "governed": False, "data_source": "pack", "origin": "no governed KPI yet", "frequency": "latest snapshot",
        "criticality": "not rated", "kpi_owner": None, "conflicting_variants": 0, "slice": None,
        "data_note": f"the pack's own risk label (Supply_Operations), routes the product is not sold on ignored · {where}",
        "value": r["red"], "value_label": f"of {r['total']} routes", "previous": r["red"], "change_basis": "none",
        "direction": direction("Routes at red risk"),
        "breakdown": r["breakdown"], "breakdown_by": r["by"], "breakdown_unit": "% of routes red",
        "chart_type": "bar", "series": [{"month": b["label"], "value": b["share_pct"]} for b in r["breakdown"]]}]
    for w in r["worst"]:
        pts = w["series"]
        t = _ready(f"On-time in full · {w['route']}", "KPI-020", "%", pts, f"OTIF per month, a route the pack marks red · supplied from {w['supply_source']}",
                   True, "red per the pack's risk status", {"kpi_owner": None, "conflicting_variants": 0, "slice": w["route"],
                                                          "risk_status": "red", "target": r["target"],
                                                          "target_label": "average of the routes in scope"})
        tiles.append(t)
    return tiles


def build_dashboard(pid: str) -> dict:
    """The dashboard for an approved proposal. Opening it for the first time records `dashboard_generated` in the audit log."""
    return compose_dashboard(gov.get_proposal(pid), record=True)


def compose_dashboard(prop: dict, record: bool = False) -> dict:
    """Same dashboard, but `record=False` has no side effects (no audit event): used to list and preview saved dashboards."""
    pid = prop["proposal_id"]
    if prop["status"] != "approved":
        raise gov.GovernanceError(
            f"{pid} is '{prop['status']}'. A dashboard is only generated after a human approves the spec.")
    spec, rec = prop["spec"], prop["recommendation"]
    if spec["action"] == "RETIRE":
        raise gov.GovernanceError(
            f"Nothing to generate: the approved action is RETIRE, so {spec.get('target_asset') or 'the asset'} is archived "
            "instead of anything being built. Next steps: " + "; ".join(spec["required_actions"]))
    products, markets = spec["scope"]["products"], spec["scope"]["markets"]

    base_asset, base_metrics, base_scope = None, set(), None
    if spec.get("reuse_asset"):
        a = _features().loc[spec["reuse_asset"]]
        base_scope = (a.product_scope, a.market_scope)
        base_asset = {"asset_id": a.asset_id, "name": a.asset_name, "platform": a.platform,
                      "endorsement": a.endorsement, "owner": a.owner_name}
        base_metrics = set(a.metrics)

    tiles = []
    slices = _slices(products, markets, max(1, len(spec["kpis"])))
    for k in spec["kpis"]:
        made = False
        for label, sp, sm in slices:
            # a slice is 'existing' only if the base asset really covers it: its product (or All Pharma) and its market (or Global)
            covered = bool(base_asset) and k["metric"] in base_metrics and \
                base_scope[0] in ("All Pharma", *(sp if len(sp) == 1 else ())) and (base_scope[1] == "Global" or (len(sm) == 1 and base_scope[1] == sm[0]))
            origin = f"existing on {base_asset['asset_id']}" if covered else "added by this build"
            res = governed_series(k["metric"], sp, sm) if k["canonical_kpi"] else None
            if res:
                unit, pts, note = res
                name = k["metric"] if label is None else f"{k['metric']} · {label}"
                tiles.append(_ready(name, k["canonical_kpi"], unit, pts, note, True, origin,
                                    {"kpi_owner": k["kpi_owner"], "conflicting_variants": k["conflicting_variants"], "slice": label}))
                made = True
        if not made:
            tiles.append({"metric": k["metric"], "state": "pending", "reason": "no_pack_data",
                          "origin": f"existing on {base_asset['asset_id']}" if base_asset and k["metric"] in base_metrics else "added by this build",
                          "kpi_id": k["canonical_kpi"], "kpi_owner": k["kpi_owner"],
                          "headline": "No data in the pack yet",
                          "message": (f"{k['canonical_kpi']} is a governed KPI, but the data pack holds no measurements for it. "
                                      "The definition and owner are real; the numbers need their source system connected.")})
    plats = spec["scope"].get("platforms", [])
    for m in rec["intent"].get("new_measures", []):
        if ungoverned_label(m) == "Routes at red risk":
            tiles += supply_tiles(products, markets)
            continue
        # a usage request that names two BI platforms is a comparison: one tile per platform
        pslices = [(pl, [pl]) for pl in plats] if len(plats) >= 2 else [(None, plats)]
        made = False
        for plabel, pl in pslices:
            res = ungoverned_series(m, products, markets, pl)
            if res:
                label, unit, pts, note = res
                tiles.append(_ready(label if plabel is None else f"{label} · {plabel}", "not a governed KPI", unit, pts, note, False,
                                    "no governed KPI yet", {"kpi_owner": None, "conflicting_variants": 0, "slice": plabel}))
                made = True
        if not made:
            tiles.append({"metric": m, "state": "pending", "reason": "not_in_pack", "origin": "to be defined",
                          "headline": "No data in the pack",
                          "message": (f"'{m}' is not a governed KPI and the data pack has no data for it "
                                      "(it covers commercial, supply, market access and BI-usage data). "
                                      "To build this, name a data source and an owner, then define the KPI (RULE-002).")})

    approval_entry = next((e for e in gov.read_audit(pid)["entries"] if e["event"] == "proposal_approved"), None)
    dec = prop["decision"]
    if record and not prop.get("dashboard_generated"):
        gov.audit("dashboard_generated", f"engine (on approval by {dec['by']})", pid,
                  {"tiles": len(tiles), "with_data": sum(t["state"] == "ready" for t in tiles),
                   "pending": sum(t["state"] == "pending" for t in tiles),
                   "approval_hash": approval_entry["hash"] if approval_entry else None})
        with gov._lock:
            s = gov._store(); s[pid]["dashboard_generated"] = True; gov._save(s)

    acct = lifecycle.accountability(prop)
    return {
        **acct,   # contacts, freshness, warnings, lifecycle (evaluated now, from the pack)
        "proposal_id": pid, "title": prop.get("name") or spec["title"], "name": prop.get("name"), "auto_title": spec["title"],
        "request": spec["request"], "verdict": spec["action"],
        "scope": spec["scope"], "tiles": tiles,
        "sample_data": False,   # kept for older UIs: no invented data anywhere any more
        "data_note": "All values come from the synthetic Bayer data pack. Nothing is invented.",
        "source": {"asset": base_asset, "model": spec.get("build_on_model")},
        "approval": {"name": dec["name"], "role": dec["role"], "at": dec["at"], "by": dec["by"],
                     "accountable_owner": dec.get("accountable_owner"),
                     "audit_hash": approval_entry["hash"] if approval_entry else None},
        "rules": rec["rules_cited"], "change_policy": spec.get("change_policy"), "next_steps": spec.get("next_steps", []),
        "required_actions": [x for x in spec["required_actions"]
                             if not (dec.get("accountable_owner") and "accountable owner" in x.lower())],
    }
