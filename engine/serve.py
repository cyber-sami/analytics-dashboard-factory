"""Run everything: Jessica's app + the recommendation API.

    .venv/bin/uvicorn engine.serve:app --port 8000
"""
from app.main import app  # noqa: F401  (Jessica's app, untouched)

from .api import router

app.include_router(router)
