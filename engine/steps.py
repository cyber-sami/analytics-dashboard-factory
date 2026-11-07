"""Next steps, tagged by WHO does them and whether they are done.

`required_actions` (plain strings) stays as it was. `next_steps` is the same list with a tag per item, so a UI can show only
what people still have to do. Statuses are honest: what the factory itself does ('factory', 'approver') is "planned" in the
draft spec and becomes "done" at approval, when the dashboard really exists. Everything that needs a real person stays "todo":
this prototype does not notify anybody, request certification or fix KPI definitions, and says so.

who: factory | approver | requester | asset_owner | kpi_owner | steward | bi_team
"""
from __future__ import annotations

import re

# (test on the action text, who, planned-or-todo). First match wins. An action that matches nothing is a bug: the eval fails.
_FACTORY, _TODO = "planned", "todo"
RULES = [
    (lambda t: t.startswith("Create a new dashboard derived from"), "factory", _FACTORY),
    (lambda t: t.startswith("In the new dashboard only:"), "factory", _FACTORY),
    (lambda t: t.startswith("In the new dashboard use the approved definitions"), "factory", _FACTORY),
    (lambda t: t.startswith("Use canonical KPI definitions only"), "factory", _FACTORY),
    (lambda t: t.startswith("Name an accountable owner before publishing"), "approver", _FACTORY),   # done by the approver at approval
    (lambda t: t.startswith("Tell "), "requester", _TODO),
    (lambda t: t.startswith("Near-duplicate "), "requester", _TODO),
    (lambda t: t.startswith("Request certification"), "steward", _TODO),
    (lambda t: t.startswith("Ask ") and " to certify " in t, "steward", _TODO),
    (lambda t: t.startswith("Ask "), "asset_owner", _TODO),   # swap KPIs / fix a refresh / move users: the owner does it
    (lambda t: t.startswith("The owner of ") and "inactive" in t, "bi_team", _TODO),
    (lambda t: " also feeds " in t and "additive" in t, "bi_team", _TODO),
    (lambda t: t.startswith("Propose to "), "asset_owner", _TODO),
    (lambda t: t.startswith("With ") and "agreement" in t, "asset_owner", _TODO),
    (lambda t: t.startswith("Keep ") and "running" in t, "asset_owner", _TODO),
    (lambda t: t.startswith("Rebuild "), "bi_team", _TODO),
    (lambda t: t.startswith("Backlog suggests"), "bi_team", _TODO),
    (lambda t: t.startswith("KPI definition conflicts to resolve"), "kpi_owner", _TODO),   # expanded per KPI below
]


# nicer wording for the tagged list (classification above still uses the original action text)
def _display(text: str) -> str:
    if text.startswith("Name an accountable owner before publishing"):
        return "Accountable owner named at approval"
    if text.startswith("Use canonical KPI definitions only"):
        return "The dashboard uses canonical KPI definitions only"
    if text.startswith("Backlog suggests: "):
        return "Plan the migration of the legacy asset: the migration backlog's suggested path is " + text.split(": ", 1)[1]
    return text


def classify(text: str):
    """(who, status) for an action text, or None if no rule knows it (the eval treats that as a failure)."""
    for test, who, status in RULES:
        if test(text):
            return who, status
    return None


def build(res: dict, target_asset: str | None = None, actions: list[str] | None = None) -> list[dict]:
    """The tagged next steps for a recommendation. Never invents work: it is the same list, tagged, plus the honest
    'nothing to do' and 'define the KPI' lines that follow from the verdict."""
    owners = [c.get("owner") for c in (res.get("change_policy") or {}).get("consult", []) if c.get("owner")]
    kpis = {k["metric"]: k for k in res.get("kpis", [])}
    steps: list[dict] = []

    def add(text, who, status, name=None):
        steps.append({"text": text, "who": who, "status": status, "who_name": name})

    for a in (actions if actions is not None else res.get("actions", [])):   # pass the spec's own list so both lists agree
        if a.startswith("KPI definition conflicts to resolve"):
            for k in res.get("kpis", []):
                n = len(k.get("conflicting_variants") or [])
                if n:
                    add(f"Resolve the {k['metric']} definition conflict ({n} variant{'s' if n != 1 else ''}): the KPI owner decides which "
                        "definitions are kept, merged or retired (RULE-002)", "kpi_owner", _TODO, k.get("owner"))
            continue
        c = classify(a)
        who, status = c if c else ("requester", _TODO)   # unknown: never claimed as done; the eval flags it
        name = next((o for o in owners if o and o in a), None)
        if name is None and a.startswith("Ask ") and who != "steward":   # 'Ask Max Virtanen to ...': the owner named in the text
            m = re.match(r"Ask (?!the owner)(.+?) to ", a)
            name = m.group(1) if m else None
        add(_display(a), who, status, name)

    verdict, intent = res.get("verdict"), res.get("intent", {})
    if verdict == "REUSE" and not any(s["status"] == _TODO for s in steps):
        add("Reused as it is: nothing is changed, nothing for anyone to do", "factory", _FACTORY)
    if verdict == "RETIRE" and target_asset:
        add(f"Named the asset to archive: {target_asset}", "factory", _FACTORY)
    if verdict == "NEW_BUILD":
        from .measures import ungoverned_label
        for m in intent.get("new_measures", []):
            if ungoverned_label(m):
                add(f"Define the KPI for '{m}' (name, unit, formula, thresholds, owner) so its tiles can become governed (RULE-002)",
                    "steward", _TODO)
            else:
                add(f"Name a data source and an owner for '{m}', then define the KPI (RULE-002)", "bi_team", _TODO)
    # the same item twice helps nobody
    seen, out = set(), []
    for s in steps:
        if s["text"] not in seen:
            seen.add(s["text"]); out.append(s)
    return out


def mark_done(steps: list[dict]) -> list[dict]:
    """At approval: what the factory (and the approver) did becomes done. People's steps stay todo."""
    return [{**s, "status": "done" if s["status"] == _FACTORY else s["status"]} for s in steps]
