"""Recommendation API. Included into the app by engine/serve.py (app/main.py stays Jessica's).

    POST /api/recommend  {"text": "...", "product_context": null, "market_context": null, "use_llm": true}
"""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .intent_llm import analyze

router = APIRouter()


class RecommendIn(BaseModel):
    text: str
    product_context: str | None = None
    market_context: str | None = None
    use_llm: bool = True


def _plain(o):
    """numpy scalars -> python, so FastAPI can serialise the result."""
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_plain(v) for v in o]
    return o.item() if hasattr(o, "item") else o


@router.post("/api/recommend")
def recommend_route(body: RecommendIn):
    return _plain(analyze(body.text, body.product_context, body.market_context, body.use_llm))




# ---------------------------------------------------------------- governance flow
from fastapi import HTTPException  # noqa: E402

from . import governance as gov  # noqa: E402


class ProposalIn(RecommendIn):
    requester: str
    answer: str | None = None                 # clarifying answer; replaces what it answers (see governance.apply_answer)
    previous_proposal_id: str | None = None   # the open proposal this answers; it is marked 'superseded'


class DecisionIn(BaseModel):
    proposal_id: str
    decision: str                      # approve | reject | request_changes
    approver_id: str                   # an active owner id, e.g. OWN-001
    comment: str = ""
    accountable_owner_id: str | None = None


@router.post("/api/proposals")
def create_proposal(body: ProposalIn):
    """Recommendation + draft spec, stored and audited, waiting for a human decision."""
    try:
        return gov.create_proposal(body.text, body.requester, body.product_context, body.market_context, body.use_llm,
                                   body.answer, body.previous_proposal_id)
    except gov.GovernanceError as e:
        raise HTTPException(404, str(e))


@router.get("/api/proposals")
def proposals():
    return gov.list_proposals()


@router.get("/api/proposals/{pid}")
def proposal(pid: str):
    try:
        return gov.get_proposal(pid)
    except gov.GovernanceError as e:
        raise HTTPException(404, str(e))


@router.post("/api/approve")
def approve(body: DecisionIn):
    try:
        return gov.decide(body.proposal_id, body.decision, body.approver_id, body.comment, body.accountable_owner_id)
    except gov.GovernanceError as e:
        raise HTTPException(409, str(e))


@router.get("/api/audit")
def audit_log(proposal_id: str | None = None):
    return gov.read_audit(proposal_id)


@router.get("/api/approvers")
def approvers():
    """Active owners who may decide (for a dropdown)."""
    o = gov.load()["Owners"]
    return o[o.status == "active"][["owner_id", "owner_name", "role", "scope"]].to_dict("records")


# ---------------------------------------------------------------- dashboard from an approved spec
from .dashboard import build_dashboard  # noqa: E402


@router.get("/api/dashboards/{pid}")
def dashboard_def(pid: str):
    """409 until a human has approved the proposal."""
    try:
        return _plain(build_dashboard(pid))
    except gov.GovernanceError as e:
        raise HTTPException(409, str(e))



# ---------------------------------------------------------------- my dashboards (saved, listed, renamed, archived)
from . import library as lib  # noqa: E402


class RenameIn(BaseModel):
    name: str
    by: str = "unknown"


class ArchiveIn(BaseModel):
    archived: bool = True
    by: str = "unknown"


@router.get("/api/dashboards")
def saved_dashboards(requester: str | None = None, archived: bool = False, decisions: bool = True):
    """Every approved dashboard (and retirement decision) on this machine, newest first. ?requester=Name for one person's."""
    return _plain(lib.list_saved(requester, archived, decisions))


@router.post("/api/dashboards/{pid}/rename")
def rename_dashboard(pid: str, body: RenameIn):
    try:
        return _plain(lib.rename(pid, body.name, body.by))
    except gov.GovernanceError as e:
        raise HTTPException(409, str(e))


@router.post("/api/dashboards/{pid}/archive")
def archive_dashboard(pid: str, body: ArchiveIn):
    """Hides it from the default list (or restores it). Nothing is deleted: the decision and the audit log stay."""
    try:
        return _plain(lib.archive(pid, body.archived, body.by))
    except gov.GovernanceError as e:
        raise HTTPException(409, str(e))


# ---------------------------------------------------------------- accountability, lifecycle, trace
from . import lifecycle as lc  # noqa: E402


@router.get("/api/lifecycle")
def lifecycle_queue():
    """Approved dashboards ordered by what needs attention (warnings, then review date)."""
    return _plain(lc.queue())


@router.get("/api/trace/{pid}")
def trace(pid: str):
    """One call for investigators: request, evidence, decision, owner, spec, audit chain."""
    try:
        return _plain(lc.trace(pid))
    except gov.GovernanceError as e:
        raise HTTPException(404, str(e))


# ---------------------------------------------------------------- home page data (pack only)
from .home import home_data  # noqa: E402


@router.get("/api/home")
def home():
    return _plain(home_data())


@router.get("/my-dashboards")
def my_dashboards_page():
    """The saved-dashboards page (served from engine/; the app can serve its own restyled copy)."""
    return FileResponse(Path(__file__).parent / "my_dashboards.html")
