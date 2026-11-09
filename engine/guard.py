"""Public-demo guard rails: per-IP rate limit, request size cap, and a daily cap on Gemini calls.

Why: the app is hosted publicly on a free-tier Gemini key. Without this, one script could burn the quota (or a
bill, if billing were ever attached). Everything is in memory (one process, resets on restart) and tunable via env:

    RATE_POST_PER_MIN  (default 20)    POST /api/* per IP per minute  (these are the calls that can reach Gemini)
    RATE_GET_PER_MIN   (default 240)   other /api/* requests per IP per minute
    MAX_BODY_BYTES     (default 20000) bigger request bodies get 413
    GEMINI_DAILY_CAP   (default 300)   Gemini calls per UTC day; past it the app answers with the rule-based parser

llm.py calls take_llm_call() before each Gemini request; when it returns False the callers fall back to rules,
exactly as they do for an outage, so nothing breaks, the AI part just takes a rest until midnight UTC.
"""
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


_lock = threading.Lock()
_hits: dict[tuple[str, str], deque] = {}   # (ip, "post"|"get") -> timestamps within the last minute
_last_sweep = 0.0
_llm_day = ""
_llm_used = 0


def llm_cap() -> int:
    return _int("GEMINI_DAILY_CAP", 300)


def take_llm_call() -> bool:
    """Reserve one Gemini call for today. False = daily cap reached (caller must use the rule-based path)."""
    global _llm_day, _llm_used
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _lock:
        if today != _llm_day:
            _llm_day, _llm_used = today, 0
        if _llm_used >= llm_cap():
            return False
        _llm_used += 1
        return True


def llm_usage() -> dict:
    with _lock:
        return {"day": _llm_day, "used": _llm_used, "cap": llm_cap()}


def _client_ip(request: Request) -> str:
    # Behind a proxy (Render etc.) the client is in a forwarded header. A client can spoof these to dodge the per-IP
    # limit, but the daily Gemini cap is the real backstop, so this is a speed bump, not a lock.
    fwd = request.headers.get("true-client-ip") or request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _allow(ip: str, kind: str, limit: int) -> bool:
    global _last_sweep
    now = time.time()
    with _lock:
        if now - _last_sweep > 300:   # drop idle IPs so the dict cannot grow forever
            _last_sweep = now
            for k in [k for k, q in _hits.items() if not q or now - q[-1] > 60]:
                del _hits[k]
        q = _hits.setdefault((ip, kind), deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/"):
            if int(request.headers.get("content-length") or 0) > _int("MAX_BODY_BYTES", 20000):
                return JSONResponse({"detail": "Request too large."}, status_code=413)
            post = request.method == "POST"
            limit = _int("RATE_POST_PER_MIN", 20) if post else _int("RATE_GET_PER_MIN", 240)
            if not _allow(_client_ip(request), "post" if post else "get", limit):
                return JSONResponse({"detail": "Too many requests, slow down a little and try again in a minute."},
                                    status_code=429, headers={"Retry-After": "60"})
        return await call_next(request)

    @app.get("/api/llm-usage")
    def llm_usage_route():
        """How much of today's Gemini budget is used (no secrets)."""
        return llm_usage()
