"""Deterministic reuse/lifecycle recommendation engine over the synthetic BI estate.

No LLM in here on purpose: every verdict is reproducible and cites the metadata,
usage, KPI, lineage and governance evidence behind it (Bayer rule 8). The LLM
only parses the request into an Intent and phrases the explanation (see llm.py).

    python -m engine.recommend REQ-0001
"""
from __future__ import annotations

import difflib
import re
import sys
from dataclasses import asdict, dataclass, field
from functools import lru_cache

import pandas as pd

from .estate import load
from .measures import find_platforms, find_ungoverned, ungoverned_label

# phrase in the request -> canonical metrics (longest phrases first)
METRIC_KEYWORDS: list[tuple[str, list[str]]] = [
    ("on time in full", ["On-Time In-Full"]),
    ("otif", ["On-Time In-Full"]),
    ("nett sales", ["Net Sales"]),
    ("sales growth", ["Net Sales Growth"]),
    ("forecast variance", ["Forecast Variance"]),
    ("forecast accuracy", ["Demand Forecast Accuracy"]),
    ("gross-to-net", ["Gross-to-Net Rate"]),
    ("budget variance", ["Budget Variance"]),
    ("market access", ["Market Access Coverage"]),
    ("reimbursement", ["Reimbursement Status Coverage"]),
    ("inventory", ["Inventory Coverage"]),
    ("supply-risk", ["On-Time In-Full", "Inventory Coverage"]),
    ("supply risk", ["On-Time In-Full", "Inventory Coverage"]),
    ("digital engagement", ["Digital Engagement Index"]),
    ("first sales", ["Net Sales"]),
    ("adoption", ["Launch Adoption Index"]),
    ("launch readiness", ["Launch Adoption Index"]),
    ("net sales", ["Net Sales"]),
    ("units", ["Units Shipped"]),
    ("performance", ["Net Sales", "Net Sales Growth"]),
]
def area_map() -> dict[str, list[str]]:
    """Therapeutic area -> its products, read from the pack (so a new area needs no code change)."""
    p = load()["Products"]
    return {a: g.product_name.tolist() for a, g in p.groupby("therapeutic_area", sort=False)}


def _area_aliases(area: str) -> set[str]:
    a = re.sub(r"\s+", " ", re.sub(r"[-_]+", " ", area.lower())).strip()
    return {a, a.replace("'", ""), a.replace("'s", ""), a.replace("'s ", " ")}   # women's health / womens health / women health


def areas_in(text: str) -> list[str]:
    """Therapeutic areas a text names ('cardiovascular medicines', 'oncology products', "women's health")."""
    t = re.sub(r"\s+", " ", re.sub(r"[-_]+", " ", text.lower()))
    return [a for a in area_map() if any(re.search(rf"(?<![a-z]){re.escape(al)}(?![a-z])", t) for al in _area_aliases(a))]
REGIONS = {"emea": "EMEA", "north america": "North America", "asia pacific": "Asia Pacific",
           "latin america": "Latin America"}
ENDORSE = {"certified": 1.0, "promoted": 0.7, "none": 0.0}
REFRESH = {"healthy": 1.0, "warning": 0.5, "failed": 0.0}

REUSE_MIN_FIT = 0.60      # best candidate must be at least this fit to be reused
RELEVANT_MIN = 0.55       # relevance needed to count as "covers the request"
TRUST_MIN = 0.5           # owner/endorsement/refresh/signals composite; below this we won't send users there
COVER_MIN = 0.5          # an asset must report at least this share of the requested KPIs
MIN_USERS = 5            # an asset nobody opens is not worth reusing
DUP_JACCARD = 0.6         # KPI-set overlap needed to call two assets near-duplicates


@dataclass
class Intent:
    raw: str
    products: list[str] = field(default_factory=list)
    markets: list[str] = field(default_factory=list)   # market ids
    metrics: list[str] = field(default_factory=list)   # canonical metric names
    legacy_replacement: bool = False
    unknown_names: list[str] = field(default_factory=list)  # products/places not found in the estate
    new_measures: list[str] = field(default_factory=list)   # clear business measures with no governed KPI (e.g. attrition)
    areas: list[str] = field(default_factory=list)          # therapeutic areas named ('Cardiovascular'): a group of products
    corrections: list[dict] = field(default_factory=list)   # obvious typos we read as a known name: [{from, to}]
    asset_ids: list[str] = field(default_factory=list)      # assets the request names directly ('AST-0183')
    ignored: list[str] = field(default_factory=list)        # scope we have no data for (e.g. 'finance department'): noted, not asked
    platforms: list[str] = field(default_factory=list)      # BI platforms named ('Tableau', 'Power BI'): a usage request is split by them


# ---------------------------------------------------------------- intent (rule-based fallback)
def _norm(s: str) -> str:
    """Lower-case and treat hyphens/underscores as spaces, so 'gross-to-net' == 'gross to net'."""
    return re.sub(r"\s+", " ", re.sub(r"[-_]+", " ", s.lower())).strip()


# capitalised words that are fine to appear after for/of/in/on without being a product or market
KNOWN_OK = {"bayer", "pharma", "tableau", "power", "bi", "emea", "global", "kpi", "the", "our", "all", "a", "an"}


def _unknown_names(text: str, known: set[str]) -> list[str]:
    """Names after 'for/of/in/on' that look like a product or place but are not in the estate."""
    out = []
    for m in re.finditer(r"\b(?:for|of|in|on)\s+((?:[A-Z][\w-]+)(?:\s+[A-Z][\w-]+)*)", text):
        name = m.group(1)
        if not all(w.lower() in known or w.isupper() for w in name.split()):
            out.append(name)
    return out


TYPO_CUTOFF = 0.8   # difflib ratio; 'Nubeca' vs 'Nubeqa' = 0.83. A judgement call, shown to the user either way.


def resolve_typos(names: list[str]) -> tuple[list[dict], list[str]]:
    """Split unknown names into (obvious typos of a known product/market, truly unknown).
    Only an unambiguous close match counts; every correction is reported back so nothing is silent."""
    d = load()
    known = {p.lower(): ("product", p) for p in d["Products"].product_name}
    known |= {m.lower(): ("market", mid) for m, mid in zip(d["Markets"].market_name, d["Markets"].market_id)}
    known |= {a.lower(): ("area", a) for a in area_map()}
    fixed, unknown = [], []
    for n in names:
        close = difflib.get_close_matches(n.strip().lower(), list(known), n=2, cutoff=TYPO_CUTOFF)
        ok = bool(close)
        if len(close) == 2:   # two near-equal candidates = ambiguous: ask instead
            r = [difflib.SequenceMatcher(None, n.strip().lower(), c).ratio() for c in close]
            ok = r[0] - r[1] >= 0.1
        if ok:
            kind, val = known[close[0]]
            fixed.append({"from": n, "to": close[0].title() if kind == "market" else val, "kind": kind, "value": val})
        else:
            unknown.append(n)
    return fixed, unknown


def known_name(answer: str) -> tuple[str, str] | None:
    """If an answer is a known product or market (exact or one clear typo away): (kind, canonical name)."""
    d = load()
    a = answer.strip().strip(".,;:!?").lower()
    for kind, names in (("product", d["Products"].product_name), ("market", d["Markets"].market_name)):
        for n in names:
            if n.lower() == a:
                return kind, n
    for area in area_map():
        if a in _area_aliases(area):
            return "area", area
    fixed, _ = resolve_typos([answer.strip().strip(".,;:!?")])
    if fixed:
        f = fixed[0]
        if f["kind"] in ("product", "area"):
            return f["kind"], f["value"]
        return "market", d["Markets"].set_index("market_id").market_name[f["value"]]
    return None


def parse_intent(text: str, product_context: str | None = None, market_context: str | None = None) -> Intent:
    """Cheap keyword parser. The LLM version should produce the same Intent shape."""
    d = load()
    t = _norm(text)
    products = [p for p in d["Products"].product_name if p.lower() in t]
    if not products and ("all pharma" in t or "bayer pharma" in t):
        products = ["All Pharma"]
    areas = areas_in(text)
    for area in areas:   # a therapeutic area is a group of products: 'cardiovascular medicines' = Kerendia, Xarelto, Beyonttra
        products += [p for p in area_map()[area] if p not in products]
    if not products and product_context:
        products = [product_context]

    markets: list[str] = []
    mk = d["Markets"]
    for word, region in REGIONS.items():
        if word in t:
            markets += mk.loc[mk.region == region, "market_id"].tolist()
    markets += [r.market_id for r in mk.itertuples() if r.market_name.lower() in t]
    # two-letter market codes written in capitals ('in the US', 'for UK'); 'IT' is skipped: it usually means the department
    markets += [r.market_id for r in mk.itertuples() if r.market_id != "IT" and re.search(rf"\b{r.market_id}\b", text)]
    if not markets and market_context and "global" not in t:
        if market_context in mk.market_id.values:
            markets = [market_context]
        elif market_context in set(mk.region.str.upper()) | {"EMEA"}:
            markets = mk.loc[mk.region.str.upper() == market_context.upper(), "market_id"].tolist()

    metrics: list[str] = []
    # 'supply risk' alone means the two KPIs on-time-in-full and inventory coverage; but when the request is clearly about
    # red ROUTES ('which routes are at red supply risk') it asks for the risk label itself, which is its own measure
    route_risk = "Routes at red risk" in find_ungoverned(text) and re.search(r"\b(red|routes?|risk status|at risk)\b", t) is not None
    for phrase, ms in METRIC_KEYWORDS:
        if route_risk and "supply" in phrase and "risk" in phrase:
            continue
        if _norm(phrase) in t:
            metrics += [m for m in ms if m not in metrics]
    known = {w for n in [*d["Products"].product_name, *d["Markets"].market_name, *d["Markets"].region,
                         *d["Products"].therapeutic_area, "medicines", "medicine", "products", "drugs", "area"]
             for w in _norm(n).split()} | KNOWN_OK
    legacy = any(w in t for w in ("tableau", "legacy", "replacement"))
    if find_ungoverned(text) and not any(w in t for w in ("legacy", "replac", "migrat")):
        legacy = False   # 'compare Tableau and Power BI report views' asks about usage, it is not a replacement request
    asset_ids = [a for a in dict.fromkeys(re.findall(r"\bAST-\d{4}\b", text.upper())) if a in _features().index]
    named_tab = [a for a in asset_ids if _features().loc[a].platform == "Tableau"]
    if named_tab:   # naming a Tableau asset = a legacy question; its product and market are the ones meant
        legacy = True
        a0 = _features().loc[named_tab[0]]
        if not products:
            products = [a0.product_scope]
        if not markets and a0.market_scope != "Global":
            markets = [a0.market_scope]
    if not metrics and re.search(r"\b(sales|revenue)\b", t):   # a generic 'sales' means Net Sales, but never overrides a specific measure
        metrics = ["Net Sales"]
    fixed, unknown = resolve_typos(_unknown_names(text, known))
    # typos anywhere, not only after for/of/in/on: a capitalised word that is not a known word but is one letter off
    # a product or market ('Nubeca' for 'Nubeqa'). Words at the start of the sentence are skipped.
    seen = {f["from"] for f in fixed} | set(unknown)
    cand = [w.strip(".,;:!?()\"'") for w in text.split()[1:]]
    cand = [w for w in cand if len(w) >= 4 and w[0].isupper() and not w.isupper() and w.lower() not in known and w not in seen]
    more, _ = resolve_typos(cand)
    fixed += more
    for f in fixed:
        if f["kind"] == "area":
            areas.append(f["value"]); products += [p for p in area_map()[f["value"]] if p not in products]
        elif f["kind"] == "product" and f["value"] not in products:
            products.append(f["value"])
        elif f["kind"] == "market" and f["value"] not in markets:
            markets.append(f["value"])
    new_measures: list[str] = []
    if not metrics and not products and not markets and not legacy and not unknown:
        # "dashboard for <something we have no KPI for>": a new measure, not a vague request.
        # (The LLM does this better; this keeps the demo working when Gemini is down.)
        m = re.search(r"\b(?:dashboard|report|tracker|view|analysis)\s+(?:for|of|on|about|showing)\s+(.+)", t)
        if m:
            phrase = re.sub(r"\s+by\s+\w+(\s+\w+)?$", "", m.group(1)).strip(" .?!")
            new_measures = [x.strip() for x in re.split(r",|\band\b", phrase) if x.strip()]
    # BI-usage measures (views, errors, load time...) are not governed KPIs, but the pack has data for them. They replace the
    # loosely worded phrase ('report load times') with their label, so a request is never counted twice.
    found = find_ungoverned(text)
    if found:
        new_measures = [m for m in new_measures if not ungoverned_label(m)] + [x.lower() for x in found]
    return Intent(raw=text, products=products, markets=sorted(set(markets)), metrics=metrics,
                  platforms=find_platforms(text), areas=list(dict.fromkeys(areas)), legacy_replacement=legacy, unknown_names=unknown, new_measures=new_measures,
                  corrections=[{"from": f["from"], "to": f["to"]} for f in fixed], asset_ids=asset_ids)


# ---------------------------------------------------------------- estate features
@lru_cache(maxsize=1)
def _features() -> pd.DataFrame:
    """One row per asset with trust / adoption / lifecycle features precomputed."""
    d = load()
    a = d["Analytics_Assets"].copy()
    owners = d["Owners"].set_index("owner_id")
    models = d["Semantic_Models"].set_index("model_id")
    sig = d["Governance_Signals"]
    sig_open = sig[sig.status == "open"].groupby("asset_id").signal_code.apply(list)
    use = d["Asset_Usage"].copy()
    months = sorted(use.month.unique())
    last3, prev3 = months[-3:], months[-6:-3]
    rec = use[use.month.isin(last3)].groupby("asset_id").agg(
        recent_users=("unique_users", "mean"), recent_views=("views", "mean"), errors=("query_errors", "sum"))
    old = use[use.month.isin(prev3)].groupby("asset_id").unique_users.mean().rename("prev_users")
    a = a.join(rec, on="asset_id").join(old, on="asset_id")
    a[["recent_users", "recent_views", "errors", "prev_users"]] = a[["recent_users", "recent_views", "errors", "prev_users"]].fillna(0)
    a["adoption"] = a.recent_users.rank(pct=True)
    a["owner_active"] = a.owner_id.map(lambda o: o in owners.index and owners.loc[o, "status"] == "active")
    a["owner_name"] = a.owner_id.map(lambda o: owners.loc[o, "owner_name"] if o in owners.index else None)
    a["refresh"] = a.model_id.map(lambda m: models.loc[m, "refresh_status"] if m in models.index else "failed")
    a["open_signals"] = a.asset_id.map(lambda x: sig_open.get(x, []))
    a["endorse_s"] = a.endorsement.map(ENDORSE).fillna(0)
    a["refresh_s"] = a.refresh.map(REFRESH).fillna(0)
    a["trust"] = (a.endorse_s + a.owner_active.astype(float) + a.refresh_s
                  + (a.open_signals.map(len) == 0).astype(float)) / 4
    a["lifecycle_s"] = a.lifecycle_status.map({"active": 1.0, "candidate_retire": 0.3, "legacy": 0.2}).fillna(0)
    # canonical metrics each asset reports, and whether it uses an unapproved variant
    km = d["Asset_KPI_Map"].merge(d["KPI_Catalog"][["kpi_id", "canonical_metric", "definition_status"]], on="kpi_id")
    a["metrics"] = a.asset_id.map(km.groupby("asset_id").canonical_metric.apply(set))
    a["variants_used"] = a.asset_id.map(
        km[km.definition_status != "canonical"].groupby("asset_id").kpi_id.apply(list))
    a["metrics"] = a.metrics.map(lambda s: s if isinstance(s, set) else set())
    a["variants_used"] = a.variants_used.map(lambda s: s if isinstance(s, list) else [])
    return a.set_index("asset_id", drop=False)


def _product_match(scope: str, products: list[str]) -> float:
    """How much of what was asked for does the asset cover? An asset about ONE product covers one of N requested
    products (1/N), not all of them: 'compare Beyonttra, Xarelto and Kerendia' is not answered by a Xarelto dashboard."""
    if not products:
        return 0.5
    if scope == "All Pharma":
        return 1.0 if "All Pharma" in products else 0.6   # generic: can cover everything, but is not about any one product
    return 1.0 / len(products) if scope in products else 0.0


def _market_match(scope: str, markets: list[str]) -> float:
    if not markets:  # no market asked for = a global view; a single-country asset is a poor match
        return 1.0 if scope == "Global" else 0.2
    if scope == "Global":
        return 0.7
    return 1.0 / len(markets) if scope in markets else 0.0   # a France-only asset covers 1 of the 7 EMEA markets


def rank_assets(intent: Intent, platform: str | None = None, exclude: set[str] = frozenset()) -> pd.DataFrame:
    f = _features()
    f = f[~f.index.isin(exclude)]
    if platform:
        f = f[f.platform == platform]
    want = set(intent.metrics)
    rows = []
    for r in f.itertuples():
        cov = len(want & r.metrics) / len(want) if want else 0.5
        pm, mm = _product_match(r.product_scope, intent.products), _market_match(r.market_scope, intent.markets)
        rel = 0.5 * cov + 0.3 * pm + 0.2 * mm
        if cov == 0 and want:
            rel = min(rel, 0.3)
        if intent.products and pm == 0:  # asset is about a different product: never relevant
            rel = min(rel, 0.3)
        fit = 0.45 * rel + 0.25 * r.trust + 0.15 * r.adoption + 0.15 * r.lifecycle_s
        rows.append((r.asset_id, rel, fit, cov, pm, mm))
    out = pd.DataFrame(rows, columns=["asset_id", "relevance", "fit", "coverage", "prod_m", "mkt_m"]).set_index("asset_id")
    return f.join(out).sort_values("fit", ascending=False)


# ---------------------------------------------------------------- evidence
def _downstream(asset_id: str, model_id: str | None) -> dict:
    e = load()["Lineage_Edges"]
    return {"downstream_assets": int((e.source_id == asset_id).sum()),
            "model_feeds_assets": int(((e.source_id == model_id) & (e.edge_type == "MODEL_TO_ASSET")).sum())}


def evidence_line(r) -> str:
    bits = [f"{r.asset_id} '{r.asset_name}' ({r.platform} {r.asset_type}, {r.lifecycle_status})",
            f"{r.endorsement} endorsement", f"~{int(r.recent_users)} avg monthly users (last 3 mo)",
            f"owner {r.owner_name or 'MISSING'}{'' if r.owner_active else ' (inactive/missing)'}",
            f"model {r.model_id} refresh {r.refresh}"]
    if r.open_signals:
        bits.append("open signals: " + ", ".join(sorted(set(r.open_signals))))
    if r.variants_used:
        bits.append("uses non-canonical KPIs " + ", ".join(r.variants_used))
    return "; ".join(bits)


def resolve_kpis(metrics: list[str]) -> list[dict]:
    k, o = load()["KPI_Catalog"], load()["Owners"].set_index("owner_id")
    out = []
    for m in metrics:
        sub = k[k.canonical_metric == m]
        can = sub[sub.definition_status == "canonical"]
        c = can.iloc[0] if len(can) else None
        out.append({
            "metric": m,
            "canonical_kpi": None if c is None else c.kpi_id,
            "approved": bool(c is not None and c.approval_status == "approved"),
            "owner": None if c is None or c.owner_id not in o.index else o.loc[c.owner_id, "owner_name"],
            "conflicting_variants": sub[sub.definition_status != "canonical"][["kpi_id", "kpi_name", "definition_status"]].to_dict("records"),
        })
    return out


def _best_model(metrics: list[str]) -> dict | None:
    """Pick a shared semantic model to build on: certified/healthy, covering the most requested KPIs."""
    d = load()
    f = _features()
    sm = d["Semantic_Models"].copy()
    cover = f.groupby("model_id").metrics.apply(lambda s: set().union(*s))
    sm["cover"] = sm.model_id.map(lambda m: len(set(metrics) & cover.get(m, set())))
    sm["score"] = (sm.cover * 2 + sm.endorsement.map(ENDORSE).fillna(0) + sm.refresh_status.map(REFRESH).fillna(0)
                   + (sm.ai_readiness == "good") * 1.0)
    if sm.empty:
        return None
    # never recommend building on a model nobody owns (RULE-003): an inactive or missing owner rules a model out,
    # unless no model with an active owner exists at all
    active = d["Owners"].set_index("owner_id").status.eq("active")
    owned = sm[sm.owner_id.map(lambda o: bool(active.get(o, False)))]
    b = (owned if len(owned) else sm).sort_values("score", ascending=False).iloc[0]
    return {"model_id": b.model_id, "name": b.model_name, "endorsement": b.endorsement, "refresh": b.refresh_status,
            "ai_readiness": b.ai_readiness, "ai_issue": b.ai_issue_pattern, "covers_metrics": int(b.cover)}


def _consult(row, why: str) -> dict:
    """An owner whose asset is involved. We inform or ask them; we never change their asset on their behalf."""
    return {"asset_id": row.asset_id, "asset_name": row.asset_name, "owner": row.owner_name or None,
            "owner_id": row.owner_id if isinstance(row.owner_id, str) else None, "why": why}


# ---------------------------------------------------------------- the verdict
def recommend(intent: Intent) -> dict:
    d = load()
    rules = {r.rule_id: r.rule_name for r in d["Governance_Rules"].itertuples()}
    kpis = resolve_kpis(intent.metrics)
    cited: dict[str, str] = {"RULE-001": rules["RULE-001"], "RULE-008": rules["RULE-008"]}
    actions: list[str] = []
    legacy_target = None

    # legacy replacement: the Tableau asset the user means defines the KPIs we need to cover
    if intent.legacy_replacement:
        tab = rank_assets(intent, platform="Tableau")
        named = tab[tab.index.isin(intent.asset_ids)]   # a request that names a Tableau asset is about that asset
        tab = tab[(tab.prod_m >= 0.6) | (not intent.products)].sort_values(["prod_m", "mkt_m", "recent_users"] if intent.markets else ["prod_m", "recent_users"],
                                                            ascending=False)   # the requested market first, only if one was named
        if len(named):
            tab = named
        if len(tab):
            legacy_target = tab.iloc[0]
            if not intent.metrics:
                intent = Intent(**{**asdict(intent), "metrics": sorted(legacy_target.metrics)})
                kpis = resolve_kpis(intent.metrics)

    ranked = rank_assets(intent, platform="Power BI" if intent.legacy_replacement else None)
    relevant = ranked[(ranked.relevance >= RELEVANT_MIN) & (ranked.coverage >= COVER_MIN)
                      & (ranked.lifecycle_status == "active") & (ranked.trust >= TRUST_MIN)
                      & (ranked.recent_users >= MIN_USERS)]
    # nothing maps to a governed KPI, or the request is about a product/place we don't know:
    # never "reuse" on neutral coverage
    if legacy_target is not None:
        # a workbook's users sit in ITS market: only an asset for the same market (or Global) can replace it
        relevant = relevant[(relevant.market_scope == legacy_target.market_scope) | (relevant.market_scope == "Global")]
    if not intent.metrics or (intent.unknown_names and not intent.products):
        relevant = relevant.iloc[0:0]
    top = ranked.iloc[0]
    evidence = [evidence_line(top)]
    verdict, headline = "NEW_BUILD", ""
    new_build = None
    chosen = None   # the asset the verdict is actually about (not just the top-ranked one)
    # Non-destructive by default: we derive, propose and ask. We never edit, merge or retire someone else's asset.
    policy = {"mode": "none", "derived_from": None, "unchanged": [], "consult": []}

    # Prefer assets that cover every requested KPI. Partial coverage is only allowed for plain
    # requests (-> EXTEND); a Tableau replacement needs full coverage or it takes the retire/migrate path.
    good = relevant[relevant.fit >= REUSE_MIN_FIT]
    full = good[good.coverage >= 0.99]
    if legacy_target is None:
        # An asset that already has the right product AND market scope and covers some KPIs (extend it)
        # beats one with every KPI but the wrong product/market (two changes needed).
        scope_ok = ((good.prod_m >= 0.99) | (not intent.products)) & (good.mkt_m >= 0.5)
        full = good[(good.coverage >= 0.99) & scope_ok]
    pool = full if (len(full) or legacy_target is not None) else good
    if len(pool):
        best = pool.iloc[0]
        chosen = best.asset_id
        partial = best.coverage < 0.99
        missing = sorted(set(intent.metrics) - best.metrics)
        jac = lambda a, b: len(a & b) / len(a | b) if a | b else 0.0
        # near-duplicates must serve the SAME market: different teams and locations need their own dashboards
        dup = relevant[(relevant.index != best.name) & (relevant.prod_m >= 0.99) & (relevant.market_scope == best.market_scope)
                       & relevant.metrics.map(lambda m: jac(m, best.metrics) >= DUP_JACCARD)]
        scope_gap = (best.market_scope not in (legacy_target.market_scope, "Global") if legacy_target is not None
                     else best.mkt_m < 0.5)   # KPIs fit, but the asset is scoped to other markets than asked for
        # the asset is about fewer products than were asked for (e.g. only Xarelto for 'Beyonttra, Xarelto and Kerendia')
        product_gap = bool(intent.products) and best.product_scope != "All Pharma" and best.prod_m < 0.99
        verdict = "EXTEND" if (partial or scope_gap or product_gap) else ("CONSOLIDATE" if len(dup) >= 1 and legacy_target is None else "REUSE")
        evidence = [evidence_line(best)]
        headline = f"Reuse {best.asset_id} '{best.asset_name}' instead of building something new."
        derive = verdict == "EXTEND"
        owner_txt = best.owner_name or "its owner"
        users = int(best.recent_users)
        want = ", ".join(intent.markets) if intent.markets else "Global"
        if derive:
            # EXTEND never edits the existing dashboard: people already use it. We build a NEW one derived from it.
            missing_products = [p for p in intent.products if p != best.product_scope]
            bits = ([f"add the missing KPIs ({', '.join(missing)})"] if partial else []) + \
                   ([f"also cover {', '.join(missing_products)}"] if product_gap else []) + \
                   ([f"cover {want} instead of {best.market_scope}"] if scope_gap else [])
            fit = (f" covers {best.coverage:.0%} of the KPIs asked for" if partial else " has the right KPIs") + \
                  (f" but is only about {best.product_scope}, while you asked for {', '.join(intent.products)}" if product_gap else "") + \
                  (f" but is scoped to {best.market_scope}, not {want}" if scope_gap else "")
            headline = (f"{best.asset_id} '{best.asset_name}'{fit}. Build on it, don't edit it: create a new dashboard "
                        f"derived from it that will {' and '.join(bits)}, and leave {best.asset_id} unchanged for its current users.")
            actions.append(f"Create a new dashboard derived from {best.asset_id} (same shared model {best.model_id}). "
                           f"Do not edit {best.asset_id}: its ~{users} monthly users keep exactly what they have.")
            if partial:
                actions.append(f"In the new dashboard only: add the missing KPIs using canonical definitions: {', '.join(missing)}")
                cited["RULE-002"] = rules["RULE-002"]
            if product_gap:
                actions.append(f"In the new dashboard only: cover {', '.join(intent.products)}, one view per product so they can be compared. "
                               f"{best.asset_id} stays about {best.product_scope}.")
            if scope_gap:
                actions.append(f"In the new dashboard only: scope it to {want}. {best.asset_id} stays scoped to {best.market_scope}.")
            actions.append(f"Tell {owner_txt}, owner of {best.asset_id}, that a derived dashboard is being created. Nothing of theirs is changed.")
            evidence.append(f"{best.asset_id} stays as it is: ~{users} monthly users, scoped to {best.market_scope}. "
                            "Different teams and locations keep their own dashboards.")
            policy = {"mode": "derive", "derived_from": best.asset_id, "unchanged": [best.asset_id],
                      "consult": [_consult(best, "owns the asset this is derived from: informed, nothing of theirs is changed")]}
        elif verdict == "REUSE":
            policy = {"mode": "reuse_as_is", "derived_from": None, "unchanged": [best.asset_id], "consult": []}
        if len(dup) and verdict in ("REUSE", "EXTEND"):
            actions += [f"Near-duplicate {x.asset_id} exists for the same product and market. Suggest to {x.owner_name or 'its owner'} "
                        "that they consider merging; this request does not change it." for x in dup.head(3).itertuples()]
        if verdict == "CONSOLIDATE":
            headline = (f"{len(dup) + 1} active assets for the same product and market ({best.market_scope}) already answer this. "
                        f"They could be consolidated onto {best.asset_id} '{best.asset_name}', but each belongs to its owner: ask, don't change.")
            evidence += [evidence_line(x) for x in dup.head(3).itertuples()]
            actions += [f"Propose to {x.owner_name or 'its owner'} (owner of {x.asset_id}, ~{int(x.recent_users)} monthly users) "
                        f"to merge into {best.asset_id}. Nothing changes without their agreement, and their users move first."
                        for x in dup.head(3).itertuples()]
            cited["RULE-006"] = rules["RULE-006"]
            policy = {"mode": "propose", "derived_from": None, "unchanged": [best.asset_id, *dup.index[:3]],
                      "consult": [_consult(x, "owns a near-duplicate: asked, nothing is changed without their agreement")
                                  for x in [best, *dup.head(3).itertuples()]]}
        if best.endorsement == "none":
            actions.append("Request certification for the new dashboard before wider rollout" if derive else
                           f"Ask {owner_txt} to certify {best.asset_id} before wider rollout")
            cited["RULE-005"] = rules["RULE-005"]
        if not best.owner_active:
            actions.append(f"The owner of {best.asset_id} is inactive: ask the BI platform owner to assign an accountable owner")
            cited["RULE-003"] = rules["RULE-003"]
        if best.variants_used:
            actions.append(("In the new dashboard use the approved definitions instead of the non-canonical ones the original uses: "
                            if derive else f"Ask {owner_txt} to swap non-canonical KPIs for approved definitions: ") + ", ".join(best.variants_used))
            cited["RULE-002"] = rules["RULE-002"]
        if best.refresh != "healthy":
            actions.append(f"Ask the owner of {best.model_id} to fix its refresh ('{best.refresh}') before relying on it")
        lin = _downstream(best.asset_id, best.model_id)
        evidence.append(f"Lineage: {best.model_id} feeds {lin['model_feeds_assets']} assets; "
                        f"{lin['downstream_assets']} downstream of {best.asset_id}")
        if derive and partial and lin["model_feeds_assets"] > 1:
            actions.append(f"{best.model_id} also feeds {lin['model_feeds_assets'] - 1} other assets: add measures there only as new, "
                           "additive ones. Never change or remove an existing measure.")
        cited["RULE-004"] = rules["RULE-004"]
        if legacy_target is not None:
            evidence.insert(0, "Legacy asset to replace: " + evidence_line(legacy_target))
            lt_owner = legacy_target.owner_name or "its owner"
            headline = (f"Don't rebuild. Power BI asset {best.asset_id} '{best.asset_name}' already covers the KPIs of "
                        f"Tableau {legacy_target.asset_id}. With {lt_owner}'s agreement, move its users over; retire it only once they have moved.")
            actions.append(f"Ask {lt_owner} (owner of {legacy_target.asset_id}, ~{int(legacy_target.recent_users)} monthly users) to point "
                           f"its users to {best.asset_id}. Retire {legacy_target.asset_id} only after they have moved.")
            cited["RULE-006"] = rules["RULE-006"]
            policy = {"mode": "retire_with_consent", "derived_from": None, "unchanged": [best.asset_id, legacy_target.asset_id],
                      "consult": [_consult(legacy_target, "owns the legacy asset: must agree before it is retired")]}
    elif legacy_target is not None:
        lt = legacy_target
        evidence = [evidence_line(lt)]
        mb = d["Migration_Backlog"]
        row = mb[mb.source_asset_id == lt.asset_id]
        path = row.iloc[0].recommended_path_candidate if len(row) else "HUMAN_REVIEW"
        low_use = lt.recent_users < _features().recent_users.median()
        cited.update({"RULE-006": rules["RULE-006"], "RULE-004": rules["RULE-004"]})
        why = f" (backlog reason: {row.iloc[0].synthetic_reason}, complexity {row.iloc[0].complexity})" if len(row) else ""
        f = _features()
        if path == "RETIRE" or (lt.lifecycle_status != "active" and low_use):
            verdict = "RETIRE"
            headline = (f"Don't migrate {lt.asset_id} '{lt.asset_name}': migrating it would be waste. "
                        f"The backlog marks it {path}, it is {lt.lifecycle_status} with ~{int(lt.recent_users)} monthly users.")
            lt_owner = lt.owner_name or "its owner"
            actions.append(f"With {lt_owner}'s agreement, and once its ~{int(lt.recent_users)} monthly users have somewhere to go: "
                           "archive it, notify downstream consumers, and tell requesters where to look instead")
            policy = {"mode": "retire_with_consent", "derived_from": None, "unchanged": [],
                      "consult": [_consult(lt, "owns the asset: must agree before it is archived")]}
        elif path == "CONSOLIDATE_BEFORE_MIGRATION":
            jac = lambda a, b: len(a & b) / len(a | b) if a | b else 0.0
            overlap = f[(f.index != lt.asset_id) & (f.product_scope == lt.product_scope) & (f.lifecycle_status != "retired")
                        & f.metrics.map(lambda m: jac(m, lt.metrics) >= 0.34)].sort_values("recent_users", ascending=False)
            # Different teams and locations need different dashboards: only assets for the SAME market are duplicates.
            dups = overlap[overlap.market_scope == lt.market_scope]
            other = overlap[overlap.market_scope != lt.market_scope]
            new_build = {"target_model": _best_model(intent.metrics), "canonical_kpis": kpis}
            lt_owner = lt.owner_name or "its owner"
            if len(dups):
                verdict = "CONSOLIDATE"
                headline = (f"Consolidate before you migrate. {lt.asset_id} '{lt.asset_name}' overlaps with {len(dups)} other "
                            f"{lt.product_scope} asset(s) for the same market ({lt.market_scope}). Their owners decide: ask, don't change.")
                evidence += [evidence_line(x) + f" (KPI overlap {jac(x.metrics, lt.metrics):.0%}, same market)" for x in dups.head(3).itertuples()]
                actions += [f"Propose to {x.owner_name or 'its owner'} (owner of {x.asset_id}, ~{int(x.recent_users)} monthly users) to merge into one shared "
                            "model before migrating once. Nothing changes without their agreement." for x in dups.head(3).itertuples()]
                policy = {"mode": "propose", "derived_from": None, "unchanged": [lt.asset_id, *dups.index[:3]],
                          "consult": [_consult(x, "owns an overlapping asset for the same market: asked, nothing is changed without their agreement")
                                      for x in [lt, *dups.head(3).itertuples()]]}
            else:
                # the backlog suggests consolidating, but everything that overlaps serves other markets: keep those
                verdict = "MIGRATE"
                headline = (f"Migrate {lt.asset_id} '{lt.asset_name}' on its own, rebuilt on a shared model for {lt.market_scope}. "
                            f"The backlog suggests consolidating first, but the {len(other)} overlapping {lt.product_scope} assets all serve "
                            "other markets with their own owners and users, so they are kept separate.")
                evidence += [evidence_line(x) + f" (KPI overlap {jac(x.metrics, lt.metrics):.0%}, but market {x.market_scope}: not touched)"
                             for x in other.head(3).itertuples()]
                actions += [f"Rebuild {lt.asset_id} for {lt.market_scope} on a shared model. Do not merge, change or retire the other-market assets.",
                            f"Keep {lt.asset_id} running until its ~{int(lt.recent_users)} monthly users have moved; ask {lt_owner} to agree."]
                policy = {"mode": "new_asset", "derived_from": None, "unchanged": [lt.asset_id, *other.index[:3]],
                          "consult": [_consult(lt, "owns the legacy asset: asked to agree before its users move")]}
        else:
            verdict, headline = "MIGRATE", f"No suitable Power BI asset exists. Migrate {lt.asset_id} by rebuilding on a shared model (not 1:1)."
            new_build = {"target_model": _best_model(intent.metrics), "canonical_kpis": kpis}
            actions.append(f"Backlog suggests: {path}")
            actions.append(f"Keep {lt.asset_id} running until its ~{int(lt.recent_users)} monthly users have moved to the new dashboard; "
                           f"ask {lt.owner_name or 'its owner'} to agree.")
            policy = {"mode": "new_asset", "derived_from": None, "unchanged": [lt.asset_id],
                      "consult": [_consult(lt, "owns the legacy asset: asked to agree before its users move")]}
        lin = _downstream(lt.asset_id, lt.model_id)
        evidence.append(f"Lineage: {lt.asset_id} has {lin['downstream_assets']} downstream assets; model {lt.model_id} feeds {lin['model_feeds_assets']}")
        evidence.append(f"Migration backlog path for {lt.asset_id}: {path}{why}")
    else:
        headline = "Nothing in the estate covers this well. Build new, on a shared governed model with canonical KPIs only."
        if not intent.metrics:
            what = f"'{', '.join(intent.new_measures)}' is not a governed KPI" if intent.new_measures else \
                "None of the requested measures exist as governed KPIs"
            headline = (f"{what} anywhere in the BI estate. "
                        "Build new: define the KPIs first, with an owner, thresholds and versioned spec.")
            if intent.new_measures and all(ungoverned_label(m) for m in intent.new_measures):
                # the pack does hold data for these: say so, a working first version follows
                headline = (f"{what} yet, but the data pack holds the data. Build it new: a first working version is "
                            "built now and marked 'not governed' until an owner defines the KPI (name, owner, thresholds, versioned spec).")
            elif intent.new_measures and not any(ungoverned_label(m) for m in intent.new_measures):
                headline += " The data pack has no data for it either, so no chart can be shown yet."
        new_build = {"target_model": _best_model(intent.metrics) if intent.metrics else None, "canonical_kpis": kpis}
        actions += ["Name an accountable owner before publishing", "Use canonical KPI definitions only"]
        cited.update({"RULE-002": rules["RULE-002"], "RULE-003": rules["RULE-003"], "RULE-007": rules["RULE-007"]})
        if intent.metrics:
            evidence = [f"Closest existing asset: {evidence_line(top)} (covers {top.coverage:.0%} of requested KPIs, relevance {top.relevance:.2f}; below reuse threshold)"]
            part = ranked[(ranked.coverage > 0) & (ranked.lifecycle_status == "active")].head(2)
            evidence += [f"Partial match ({x.coverage:.0%} KPI coverage): {x.asset_id} '{x.asset_name}', {x.endorsement}" for x in part.itertuples()]
        else:
            evidence = [f"Searched {len(_features())} assets and {len(d['KPI_Catalog'])} KPI definitions: "
                        "no governed KPI matches the requested measures."]

    for m in intent.new_measures:
        lab = ungoverned_label(m)
        evidence.append(f"The data pack holds data for '{m}' ({lab}), so a first version can be built right away. It is marked "
                        "'not governed' until an owner defines the KPI (RULE-002)." if lab else
                        f"The data pack holds no data for '{m}'. It would need a data source and an owner first.")
    for a in intent.areas:
        evidence.append(f"Therapeutic area {a}: {', '.join(area_map()[a])} (from the data pack). Several products are shown side by side.")
    for c in intent.corrections:
        evidence.append(f"Read '{c['from']}' as '{c['to']}' (closest match in the estate; tell us if that is wrong).")
    if intent.ignored:
        evidence.append("Not used as a scope: " + ", ".join(f"'{x}'" for x in intent.ignored)
                        + " (the data pack has no organisational breakdown, only products and markets).")
    if intent.unknown_names:
        evidence.append("Not found in the estate: " + ", ".join(intent.unknown_names))

    clarify: list[str] = []
    if not intent.metrics and legacy_target is None and not intent.new_measures:
        clarify.append("Which measure do you need (for example net sales, forecast accuracy, market access coverage)?")
    if intent.unknown_names:
        prods = ", ".join(d["Products"].product_name)
        mk = d["Markets"].market_name.tolist()
        areas_txt = "; ".join(f"{a} ({', '.join(ps)})" for a, ps in area_map().items())
        clarify.append(f"I couldn't find {', '.join(intent.unknown_names)} in this analytics estate. It covers these products: "
                       f"{prods}; these therapeutic areas: {areas_txt}; and {len(mk)} markets (for example {', '.join(mk[:4])}). "
                       "Which one did you mean?")
    elif not intent.products and not intent.new_measures and intent.metrics != ["Report Adoption"]:
        # a brand-new measure is not tied to a pharma product, and BI usage is about the estate as a whole
        clarify.append("Which product, therapeutic area (for example cardiovascular or oncology) or all of Pharma is this for?")
    if intent.legacy_replacement and not intent.products:
        clarify.append("Which legacy Tableau workbook should be replaced?")

    conflicts = [k for k in kpis if k["conflicting_variants"]]
    if conflicts:
        cited["RULE-002"] = rules["RULE-002"]
        actions.append("KPI definition conflicts to resolve: " + ", ".join(
            f"{k['metric']} ({len(k['conflicting_variants'])} variants)" for k in conflicts))

    show = pd.concat([relevant, ranked]).loc[lambda x: ~x.index.duplicated()]
    if chosen:  # the asset the headline is about always comes first, so headline and table agree
        show = pd.concat([show.loc[[chosen]], show.drop(index=chosen)])
    show = show.head(5)
    return {
        "verdict": verdict, "headline": headline, "intent": asdict(intent), "kpis": kpis,
        "evidence": evidence, "actions": actions, "clarify": clarify, "chosen_asset": chosen, "change_policy": policy, "rules_cited": cited, "new_build": new_build,
        "candidates": [{"asset_id": i, "name": r.asset_name, "platform": r.platform, "status": r.lifecycle_status,
                        "endorsement": r.endorsement, "relevance": round(r.relevance, 2), "fit": round(r.fit, 2),
                        "chosen": i == chosen}
                       for i, r in show.iterrows()],
    }


def recommend_text(text: str, product_context: str | None = None, market_context: str | None = None) -> dict:
    return recommend(parse_intent(text, product_context, market_context))


if __name__ == "__main__":
    reqs = load()["Business_Requests"].set_index("request_id")
    rid = sys.argv[1] if len(sys.argv) > 1 else "REQ-0001"
    r = reqs.loc[rid]
    print(f"{rid} [{r.urgency}]: {r.request_text}  (ctx: {r.product_context}/{r.market_context})\n")
    res = recommend_text(r.request_text, r.product_context, r.market_context)
    print(f"VERDICT: {res['verdict']}\n{res['headline']}\n")
    print("intent:", {k: v for k, v in res["intent"].items() if k != "raw"})
    print("\nEVIDENCE:"); [print(" -", e) for e in res["evidence"]]
    print("\nACTIONS:"); [print(" -", a) for a in res["actions"]]
    print("\nRULES:", ", ".join(f"{k} {v}" for k, v in res["rules_cited"].items()))
    print("\nTOP CANDIDATES:"); [print("  ", c) for c in res["candidates"][:3]]
