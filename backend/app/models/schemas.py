"""API request/response models (thin; domain state lives in graph.state)."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.bus.schemas import ReviewAction


class IncidentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    severity: str = Field(default="high", pattern="^(low|medium|high|critical)$")
    source: str = "api"
    service: str | None = None
    error: str | None = None
    message: str | None = None
    target_file: str | None = None
    files: dict[str, str] = Field(default_factory=dict)
    raw_payload: dict = Field(default_factory=dict)
    auto_run: bool = False


class ReviewRequest(BaseModel):
    action: ReviewAction
    reason: str | None = None
    patch_ref: str | None = None
    modified_files: dict[str, str] = Field(default_factory=dict)


class SandboxWarmRequest(BaseModel):
    count: int | None = Field(default=None, ge=1, le=10)
