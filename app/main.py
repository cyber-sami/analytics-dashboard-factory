from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="Analytics Dashboard Factory")


@app.get("/")
def index():
    # Data for this page comes from the engine's /api/home (synthetic data pack only).
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/pipeline")
def pipeline():
    return FileResponse(ROOT / "static" / "pipeline.html")


@app.get("/trace/{pid}")
def trace_page(pid: str):
    # One page for an investigator; data comes from the engine's /api/trace/{pid}.
    return FileResponse(ROOT / "static" / "trace.html")


@app.get("/dashboard/{pid}")
def generated_dashboard(pid: str):
    # Page is ours (static/dashboard.html, same look as the home dashboard); the data still comes from
    # engine's /api/dashboards/{pid}. Registered here first, so it wins over engine/api.py's own page route.
    return FileResponse(ROOT / "static" / "dashboard.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
