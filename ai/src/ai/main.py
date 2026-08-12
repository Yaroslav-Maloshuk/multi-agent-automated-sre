"""FastAPI application entrypoint for the AI gateway."""

from __future__ import annotations

from fastapi import FastAPI

from . import __version__
from .router import router

app = FastAPI(
    title="auto-sre AI gateway",
    version=__version__,
    description="LiteLLM routing, structured calling (Instructor/Outlines/DSPy), hybrid RAG, ETL and RAGAS evals.",
)
app.include_router(router, prefix="")


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "auto-sre-ai-gateway", "version": __version__, "docs": "/docs"}
