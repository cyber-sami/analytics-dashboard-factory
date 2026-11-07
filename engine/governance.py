"""Spec generation, human approval and a tamper-evident audit log.

Flow:  request -> create_proposal (recommendation + draft spec, audited)
              -> decide (approve / reject / request_changes by an accountable human, audited)

Governance rules enforced here (not by the LLM):
  - only an active owner from the Owners table can decide                (RULE-003)
  - four-eyes: the requester cannot approve their own request
  - a proposal with open clarifying questions cannot be approved
  - every approval needs a named, active accountable owner (reuse, extend, consolidate, migrate, retire, build)
  - every step is written to an append-only, hash-chained audit log      (RULE-008)
State lives in engine/runtime/ (git-ignored): proposals.json, audit.jsonl.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .estate import load
from .intent_llm import analyze
from .recommend import known_name
from . import steps as steps_mod

RUNTIME = Path(__file__).parent / "runtime"
DECISIONS = {"approve": "approved", "reject": "rejected", "request_changes": "changes_requested"}
_lock = threading.Lock()


class GovernanceError(Exception):
    """A governance rule blocked the action. Message is safe to show to the user."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ audit log (hash chain)
def _audit_path() -> Path:
    RUNTIME.mkdir(exist_ok=True)
    return RUNTIME / "audit.jsonl"


def _hash(entry: dict) -> str:
    return hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()


def audit(event: str, actor: str, proposal_id: str | None, detail: dict) -> dict:
    """Append an entry. Each entry commits to the previous one, so edits or deletions are detectable."""
    with _lock:
        p = _audit_path()
        lines = p.read_text().splitlines() if p.exists() else []
        prev = json.loads(lines[-1])["hash"] if lines else "GENESIS"
        entry = {"seq": len(lines) + 1, "ts": _now(), "event": event, "actor": actor,
                 "proposal_id": proposal_id, "detail": detail, "prev_hash": prev}
        entry["hash"] = _hash(entry)
        with p.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    return entry


def read_audit(proposal_id: str | None = None) -> dict:
    p = _audit_path()
    entries = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
    ok, prev = True, "GENESIS"
    for e in entries:  # verify the whole chain, not just the filtered view
        body = {k: v for k, v in e.items() if k != "hash"}
        if e["prev_hash"] != prev or _hash(body) != e["hash"]:
            ok = False
        prev = e["hash"]
    if proposal_id:
        entries = [e for e in entries if e["proposal_id"] == proposal_id]
    return {"chain_valid": ok, "count": len(entries), "entries": entries}


# ------------------------------------------------------------------ spec
def build_spec(res: dict) -> dict:
    """Draft spec for the human to approve. Deterministic: built from the cited evidence only."""
    v, intent = res["verdict"], res["intent"]
    scope = ", ".join(intent["products"])
    spec = {
        "status": "draft",
        "action": v,
        "title": f"{v.replace('_', ' ').title()}: {', '.join(intent['metrics'] + intent.get('new_measures', [])) or 'new measures'}{" for " + scope if scope else ""}",
        "request": intent["raw"],
        "scope": {"products": intent["products"], "markets": intent["markets"], "platforms": intent.get("platforms", [])},
        "kpis": [{"metric": k["metric"], "canonical_kpi": k["canonical_kpi"], "approved_definition": k["approved"],
                  "kpi_owner": k["owner"], "conflicting_variants": len(k["conflicting_variants"])} for k in res["kpis"]],
        "target_asset": (re.search(r"AST-\d+", res["headline"]) or [None])[0],   # the asset the verdict is about
        "reuse_asset": res.get("chosen_asset") if v in {"REUSE", "EXTEND", "CONSOLIDATE"} else None,
        "build_on_model": (res["new_build"] or {}).get("target_model"),
        # archiving needs no KPI clean-up: keep the retirement steps only
        "change_policy": res.get("change_policy"),   # what is derived, what stays unchanged, whose owner is consulted
        # the same list as required_actions, tagged by who does each item; "planned" until approval (see engine/steps.py)
        "next_steps": steps_mod.build(res, (re.search(r"AST-\d+", res["headline"]) or [None])[0],
                                      [x for x in res["actions"] if not (v == "RETIRE" and x.startswith("KPI definition conflicts"))]),
        "required_actions": ([x for x in res["actions"] if not x.startswith("KPI definition conflicts")]
                             if v == "RETIRE" else res["actions"]),
        "governance_rules": list(res["rules_cited"]),
        "preview": {"layout": "KPI tiles on top, trend per KPI below, owner and certification badge in the header",
                    "refresh": "inherits the shared semantic model refresh"},
    }
    return spec


# ------------------------------------------------------------------ proposals
def _store() -> dict:
    p = RUNTIME / "proposals.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _save(store: dict) -> None:
    RUNTIME.mkdir(exist_ok=True)
    (RUNTIME / "proposals.json").write_text(json.dumps(store, indent=1))


def _plain(o):
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_plain(v) for v in o]
    return o.item() if hasattr(o, "item") else o


def _known_names_in(answer: str) -> list[tuple[str, str]]:
    """Every known product/market named in a free-text answer ('I mean Kerendia in Germany'), in order."""
    words = [w.strip(".,;:!?()\"'") for w in answer.split()]
    hits, i = [], 0
    while i < len(words):
        hit = known_name(" ".join(words[i:i + 2])) if i + 1 < len(words) else None   # two-word names first
        if hit:
            hits.append(hit); i += 2; continue
        hit = known_name(words[i]) if len(words[i]) >= 4 else None
        if hit:
            hits.append(hit)
        i += 1
    return hits


FILLER = {"i", "im", "i'm", "mean", "meant", "want", "wanted", "my", "the", "a", "an", "it", "is", "was", "please", "thanks",
          "sorry", "yes", "no", "ok", "okay", "sure", "of", "for", "in", "on", "at", "and", "or", "to", "product", "market",
          "country", "brand", "region", "medicine", "drug", "medicines", "products", "drugs", "area", "therapeutic", "therapy"}


def apply_answer(previous: dict, answer: str) -> str:
    """Fold a clarifying answer into the earlier request, so it can never loop.
    - Known products/markets in the answer REPLACE the unknown names they answer, matched by kind: a product goes into
      a 'for/of <unknown>' slot, a market into 'in/on/at <unknown>'. A name of the wrong kind is ignored and the
      question stays open (we never plug a product into a market slot).
    - Words left over once names and filler are removed ('net sales' in 'net sales for Kerendia') are added.
    - If the earlier request understood nothing at all (gibberish) and the answer is a full request, it replaces it."""
    intent = previous["recommendation"]["intent"]
    text, unknown, ans = intent["raw"], list(intent["unknown_names"]), answer.strip()
    unknown_before = bool(unknown)
    hits = _known_names_in(ans)
    understood = any(intent[k] for k in ("products", "markets", "metrics", "new_measures")) or intent["legacy_replacement"]
    hit_words = {w.lower() for kind, c in hits for w in c.split()}
    leftover = [w for w in (x.strip(".,;:!?()\"'") for x in ans.split())
                if w and w.lower() not in FILLER and w.lower() not in hit_words and known_name(w) is None]
    if not understood and leftover:
        return ans   # the first request meant nothing; the answer is the real request
    replaced = False
    for kind, canonical in hits:
        prefer = ("for", "of") if kind in ("product", "area") else ("in", "on", "at")
        def preposition(name):
            m = re.search(rf"\b(\w+)\s+{re.escape(name)}\b", text, flags=re.I)
            return m.group(1).lower() if m else ""
        fits = [u for u in unknown if preposition(u) in prefer]
        slot_free = not intent["products"] if kind in ("product", "area") else not intent["markets"]
        target = fits[0] if fits else (unknown[0] if unknown and slot_free else None)
        if target:
            text = re.sub(rf"\b{re.escape(target)}\b", canonical, text, count=1, flags=re.I)
            unknown.remove(target); replaced = True
    if hits and not leftover:
        if replaced or unknown_before:
            return text   # it fit (replaced a name) or it was the wrong kind (ignored): the question stays open if so
        return f"{text} {ans}".strip()   # no unknown name was being answered ('which product?'): just add it
    extra = " ".join(leftover) if replaced else ans
    return f"{text} {extra}".strip()


def create_proposal(text: str, requester: str, product_context: str | None = None,
                    market_context: str | None = None, use_llm: bool = True,
                    answer: str | None = None, previous_proposal_id: str | None = None) -> dict:
    """With `answer` + `previous_proposal_id` this is a clarification round: the answer is folded into the earlier
    request (see apply_answer) and the earlier, still-open proposal is marked superseded."""
    previous = None
    if answer and previous_proposal_id:
        previous = get_proposal(previous_proposal_id)
        text = apply_answer(previous, answer)
    res = _plain(analyze(text, product_context, market_context, use_llm))
    pid = "PRP-" + uuid.uuid4().hex[:8].upper()
    prop = {"proposal_id": pid, "created": _now(), "requester": requester, "status": "pending_approval",
            "recommendation": res, "spec": build_spec(res), "decision": None}
    if previous:
        prop["clarifies"] = previous["proposal_id"]
    with _lock:
        s = _store(); s[pid] = prop
        if previous and s.get(previous["proposal_id"], {}).get("status") == "pending_approval":
            s[previous["proposal_id"]]["status"] = "superseded"
        _save(s)
    detail = {"verdict": res["verdict"], "headline": res["headline"], "evidence": res["evidence"],
              "rules": list(res["rules_cited"]), "intent": res["intent"], "llm": use_llm}
    if previous:
        detail |= {"clarifies": previous["proposal_id"], "answer": answer}
        audit("proposal_superseded", requester, previous["proposal_id"], {"replaced_by": pid, "answer": answer})
    audit("recommendation_generated", requester, pid, detail)
    return prop


def get_proposal(pid: str) -> dict:
    p = _store().get(pid)
    if not p:
        raise GovernanceError(f"Unknown proposal {pid}")
    return p


def list_proposals() -> list[dict]:
    return sorted(_store().values(), key=lambda p: p["created"], reverse=True)


def decide(pid: str, decision: str, approver_id: str, comment: str = "",
           accountable_owner_id: str | None = None) -> dict:
    if decision not in DECISIONS:
        raise GovernanceError(f"decision must be one of {sorted(DECISIONS)}")
    owners = load()["Owners"].set_index("owner_id")
    if approver_id not in owners.index or owners.loc[approver_id, "status"] != "active":
        raise GovernanceError(f"{approver_id} is not an active owner and cannot decide (RULE-003).")
    approver = owners.loc[approver_id]
    with _lock:
        s = _store()
        prop = s.get(pid)
        if not prop:
            raise GovernanceError(f"Unknown proposal {pid}")
        if prop["status"] != "pending_approval":
            raise GovernanceError(f"{pid} is already {prop['status']}.")
        rec, owner_name, owner_oid = prop["recommendation"], None, None
        if decision == "approve":
            if prop["requester"].strip().lower() in {approver_id.lower(), str(approver.owner_name).lower()}:
                raise GovernanceError("Four-eyes rule: you cannot approve your own request.")
            if rec["clarify"]:
                raise GovernanceError("Open questions must be answered before approval: " + " | ".join(rec["clarify"]))
            # every approved decision (reuse, extend, consolidate, migrate, retire, build) has ONE named, active
            # person who answers for it: someone must own the outcome, including a retirement.
            oid = accountable_owner_id or ""
            if oid not in owners.index or owners.loc[oid, "status"] != "active":
                raise GovernanceError(f"Name an active accountable owner before approving this {rec['verdict'].replace('_', ' ').lower()}"
                                      " (RULE-003): someone has to answer for the outcome.")
            owner_name, owner_oid = owners.loc[oid, "owner_name"], oid
        if decision != "approve" and not comment.strip():
            raise GovernanceError("Please give a short reason when rejecting or requesting changes.")
        prop["status"] = DECISIONS[decision]
        prop["decision"] = {"by": approver_id, "approver_id": approver_id, "name": approver.owner_name, "role": approver.role, "at": _now(),
                            "decision": decision, "comment": comment, "accountable_owner": owner_name,
                            "accountable_owner_id": owner_oid}
        if decision == "approve":
            prop["spec"]["next_steps"] = steps_mod.mark_done(prop["spec"].get("next_steps", []))
            prop["spec"]["status"] = "approved"
            prop["spec"]["accountable_owner"] = owner_name
        _save(s)
    audit(f"proposal_{DECISIONS[decision]}", f"{approver_id} ({approver.owner_name}, {approver.role})", pid,
          {"comment": comment, "accountable_owner": owner_name, "verdict": rec["verdict"]})
    return prop
