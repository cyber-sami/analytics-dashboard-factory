"""Accountability and lifecycle for approved proposals: who answers for it, who fixes it, is it still healthy.

Everything is read from the synthetic data pack AT READ TIME (so an owner who is inactive in the pack is flagged
every time, not just at approval). Two policy parameters are ASSUMPTIONS, not Bayer policy, and are labelled so:
  REVIEW_MONTHS    review a dashboard every N months after approval
  LOW_USE_QUANTILE an asset in the bottom quartile of monthly users across the estate is a retire candidate (RULE-006)
"""
from __future__ import annotations

import pandas as pd

from . import governance as gov
from .estate import load
from .recommend import _features

REVIEW_MONTHS = 3
LOW_USE_QUANTILE = 0.25
REVIEW_POLICY = f"every {REVIEW_MONTHS} months (assumption, not Bayer policy)"


def _owner_row(oid):
    o = load()["Owners"].set_index("owner_id")
    return o.loc[oid] if isinstance(oid, str) and oid in o.index else None


def _contact(role: str, oid, why: str) -> dict:
    r = _owner_row(oid)
    if r is None:
        return {"role": role, "name": None, "owner_id": oid if isinstance(oid, str) else None, "owner_role": None,
                "email": None, "status": "missing", "why": why}
    return {"role": role, "name": r.owner_name, "owner_id": oid, "owner_role": r.role, "email": r.email,
            "status": r.status, "why": why}


def _owner_id_by_name(name):
    o = load()["Owners"]
    m = o[o.owner_name == name]
    return m.iloc[0].owner_id if len(m) else None


def accountability(prop: dict) -> dict:
    """contacts, freshness, warnings and lifecycle for an approved proposal. Evaluated now, from the pack."""
    spec, dec = prop["spec"], prop["decision"] or {}
    d = load()
    feats = _features()
    base = spec.get("reuse_asset") or spec.get("target_asset")
    asset = feats.loc[base] if base and base in feats.index else None

    model_id = asset.model_id if asset is not None else (spec.get("build_on_model") or {}).get("model_id")
    models = d["Semantic_Models"].set_index("model_id")
    model = models.loc[model_id] if model_id in models.index else None

    contacts = []
    acc_id = dec.get("accountable_owner_id") or _owner_id_by_name(dec.get("accountable_owner"))
    if acc_id or dec.get("accountable_owner"):
        contacts.append(_contact("Accountable owner", acc_id, "answers for decisions made from this dashboard"))
    if model is not None:
        contacts.append(_contact("Data model owner", model.owner_id, f"owns {model_id}, the semantic model it reads"))
    if asset is not None:
        verb = {"EXTEND": "is derived from (it is not modified)", "REUSE": "is reused as it is"}.get(
            spec["action"], "is the subject of this decision")
        contacts.append(_contact("Asset owner", asset.owner_id, f"owns {base}, which this {verb}"))
    seen = set()
    for k in spec.get("kpis", []):
        oid = _owner_id_by_name(k.get("kpi_owner"))
        if oid and oid not in seen and len(seen) < 3:   # cap: the first three KPI owners are enough to call
            seen.add(oid)
            contacts.append(_contact("KPI owner", oid, f"owns the definition of {k['metric']}"))

    warnings = []
    for c in contacts:
        if c["status"] == "inactive":
            warnings.append(f"{c['role']} {c['name']} is no longer active: reassign before relying on this dashboard.")
        elif c["status"] == "missing":
            warnings.append(f"{c['role']} is missing: no accountable person is recorded (RULE-003).")
    freshness = None
    if model is not None:
        freshness = {"model_id": model_id, "model_name": model.model_name, "refresh_frequency": model.refresh_frequency,
                     "refresh_status": model.refresh_status, "ai_readiness": model.ai_readiness}
        if model.refresh_status != "healthy":
            warnings.append(f"Refresh of {model_id} is '{model.refresh_status}': the data behind this may be stale.")
    if asset is not None:
        if asset.lifecycle_status != "active":
            warnings.append(f"{base} is '{asset.lifecycle_status}' in the estate.")
        if asset.open_signals:
            warnings.append(f"Open governance signals on {base}: {', '.join(sorted(set(asset.open_signals)))}.")

    # lifecycle: usage and retire signal come from the asset this builds on
    approved_at = dec.get("at")
    review_due = None
    if approved_at:
        review_due = (pd.Timestamp(approved_at) + pd.DateOffset(months=REVIEW_MONTHS)).strftime("%Y-%m-%d")
    usage, retire_signal = None, None
    if asset is not None:
        avg = round(float(asset.recent_users), 1)
        cutoff = float(feats.recent_users.quantile(LOW_USE_QUANTILE))
        usage = {"avg_monthly_users_3m": avg, "source": f"Asset_Usage for {base}",
                 "estate_low_use_cutoff": round(cutoff, 1)}
        if avg <= cutoff:
            retire_signal = (f"Low use: ~{avg} monthly users, in the bottom {int(LOW_USE_QUANTILE * 100)}% of the estate "
                             f"(judgement call). Consider RETIRE or CONSOLIDATE (RULE-006).")
            warnings.append(retire_signal)
    state = {"RETIRE": "retirement approved"}.get(spec["action"], "review recommended" if retire_signal or warnings else "active")
    return {"contacts": contacts, "freshness": freshness, "warnings": warnings,
            "lifecycle": {"state": state, "approved_at": approved_at, "review_due": review_due,
                          "review_policy": REVIEW_POLICY, "usage": usage, "retire_signal": retire_signal}}


def queue() -> list[dict]:
    """Approved proposals ordered by what needs attention first (more warnings, then earlier review date)."""
    rows = []
    for p in gov.list_proposals():
        if p["status"] != "approved":
            continue
        a = accountability(p)
        rows.append({"proposal_id": p["proposal_id"], "title": p["spec"]["title"], "verdict": p["spec"]["action"],
                     "state": a["lifecycle"]["state"], "review_due": a["lifecycle"]["review_due"],
                     "review_policy": REVIEW_POLICY, "usage": a["lifecycle"]["usage"],
                     "retire_signal": a["lifecycle"]["retire_signal"], "warnings": a["warnings"],
                     "accountable_owner": next((c["name"] for c in a["contacts"] if c["role"] == "Accountable owner"), None)})
    rows.sort(key=lambda r: (-len(r["warnings"]), r["review_due"] or ""))
    return rows


def trace(pid: str) -> dict:
    """Everything an investigator needs about one decision, in one response."""
    prop = gov.get_proposal(pid)
    rec = prop["recommendation"]
    audit = gov.read_audit(pid)
    out = {
        "proposal_id": pid, "status": prop["status"],
        "request": {"text": rec["intent"]["raw"], "requester": prop["requester"], "at": prop["created"]},
        "understood_as": rec["intent"], "clarifying_questions": rec["clarify"],
        "recommendation": {"verdict": rec["verdict"], "headline": rec["headline"], "evidence": rec["evidence"],
                           "actions": rec["actions"], "rules_cited": rec["rules_cited"],
                           "candidates": rec["candidates"]},
        "decision": prop["decision"], "spec_snapshot": prop["spec"],
        "dashboard_generated": next((e for e in audit["entries"] if e["event"] == "dashboard_generated"), None),
        "audit": {"chain_valid": audit["chain_valid"], "entries": audit["entries"]},
    }
    if prop["status"] == "approved":
        out["accountability"] = accountability(prop)
    return out
