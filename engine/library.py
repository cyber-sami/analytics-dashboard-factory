"""'My dashboards': every approved dashboard (and retirement decision) saved on this machine, listed, renamed, archived.

Nothing here deletes anything: the proposal, its decision and the audit chain stay. Archiving only hides an entry from the
default list, and renaming only changes the display name; both are written to the audit log like everything else.
Dashboards persist because proposals persist (engine/runtime/proposals.json): they survive a page refresh and a server restart.
"""
from __future__ import annotations

from . import governance as gov
from .dashboard import compose_dashboard

MAX_NAME = 80


def _preview(d: dict) -> dict | None:
    """A compact first chart for the card: the first tile that has a monthly series."""
    for t in d["tiles"]:
        if t["state"] == "ready" and t.get("kind") != "snapshot" and t.get("series"):
            return {"metric": t["metric"], "unit": t["unit"], "value": t["value"], "previous": t["previous"],
                    "change_basis": t["change_basis"], "direction": t["direction"], "values": [p["value"] for p in t["series"]]}
    return None


def _item(prop: dict) -> dict:
    spec, dec = prop["spec"], prop["decision"] or {}
    base = {"proposal_id": prop["proposal_id"], "request": spec["request"], "verdict": spec["action"], "requester": prop["requester"],
            "created": prop["created"], "approved_at": dec.get("at"), "approved_by": dec.get("name"),
            "accountable_owner": dec.get("accountable_owner"), "archived": bool(prop.get("archived")),
            "name": prop.get("name") or spec["title"], "custom_name": prop.get("name"), "auto_title": spec["title"],
            "trace_url": f"/trace/{prop['proposal_id']}",
            "todo_count": sum(1 for s in spec.get("next_steps", []) if s["status"] == "todo")}
    if spec["action"] == "RETIRE":   # a retirement archives an asset, it builds no dashboard
        return {**base, "kind": "decision", "url": None, "tiles": 0, "warnings": 0, "preview": None, "review_due": None}
    d = compose_dashboard(prop)   # no audit side effect
    return {**base, "kind": "dashboard", "url": f"/dashboard/{prop['proposal_id']}",
            "tiles": sum(t["state"] == "ready" for t in d["tiles"]), "warnings": len(d["warnings"]),
            "review_due": d["lifecycle"]["review_due"], "state": d["lifecycle"]["state"], "preview": _preview(d)}


def list_saved(requester: str | None = None, archived: bool = False, decisions: bool = True) -> list[dict]:
    """Approved dashboards, newest first. `requester` filters to one person's; `archived=True` lists the hidden ones instead."""
    out = []
    for p in gov.list_proposals():
        if p["status"] != "approved" or bool(p.get("archived")) != archived:
            continue
        if requester and p["requester"].strip().lower() != requester.strip().lower():
            continue
        if p["spec"]["action"] == "RETIRE" and not decisions:
            continue
        out.append(_item(p))
    return sorted(out, key=lambda i: i["approved_at"] or "", reverse=True)


def _approved(pid: str) -> dict:
    p = gov.get_proposal(pid)
    if p["status"] != "approved":
        raise gov.GovernanceError(f"{pid} is '{p['status']}': only approved dashboards are saved.")
    return p


def _update(pid: str, **fields) -> None:
    with gov._lock:
        s = gov._store()
        s[pid].update(fields)
        gov._save(s)


def rename(pid: str, name: str, by: str = "unknown") -> dict:
    name = " ".join((name or "").split())
    if not 3 <= len(name) <= MAX_NAME:
        raise gov.GovernanceError(f"Give it a name of 3 to {MAX_NAME} characters.")
    p = _approved(pid)
    old = p.get("name") or p["spec"]["title"]
    _update(pid, name=name)
    gov.audit("dashboard_renamed", by, pid, {"from": old, "to": name})
    return _item(gov.get_proposal(pid))


def archive(pid: str, archived: bool = True, by: str = "unknown") -> dict:
    p = _approved(pid)
    _update(pid, archived=archived)
    gov.audit("dashboard_archived" if archived else "dashboard_restored", by, pid, {"name": p.get("name") or p["spec"]["title"]})
    return _item(gov.get_proposal(pid))
