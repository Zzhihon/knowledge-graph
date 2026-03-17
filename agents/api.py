"""FastAPI application for the Knowledge Graph Web UI.

Wraps existing agents/ modules as REST endpoints and serves
the built frontend as static files in production mode.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from agents.api_routes import stats, search, quiz, health, graph, export, ingest, sync, conversations, entries, distill, problems, rss, domains

app = FastAPI(title="Knowledge Graph API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:8765"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routers
app.include_router(stats.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(quiz.router, prefix="/api")
app.include_router(health.router, prefix="/api")
app.include_router(graph.router, prefix="/api")
app.include_router(export.router, prefix="/api")
app.include_router(ingest.router, prefix="/api")
app.include_router(sync.router, prefix="/api")
app.include_router(conversations.router, prefix="/api")
app.include_router(entries.router, prefix="/api")
app.include_router(distill.router, prefix="/api")
app.include_router(problems.router, prefix="/api")
app.include_router(rss.router, prefix="/api")
app.include_router(domains.router, prefix="/api")

# Serve built frontend in production (must be last so /api routes take priority)
_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="frontend")
