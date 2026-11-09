"""Run everything: Jessica's app + the recommendation API.

    .venv/bin/uvicorn engine.serve:app --port 8000
"""
from app.main import app  # noqa: F401  (Jessica's app, untouched)

from .api import router
from .guard import install as install_guard

app.include_router(router)
install_guard(app)   # per-IP rate limit, size cap, daily Gemini cap (public hosting)
