"""Incident lifecycle REST surface."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_incident_service
from app.bus.schemas import HITLReview
from app.models.schemas import IncidentCreate, ReviewRequest
from app.services.incident_service import IncidentService

router = APIRouter()


@router.post("/incidents", status_code=201)
async def create_incident(
    payload: IncidentCreate, service: IncidentService = Depends(get_incident_service)
) -> dict:
    result = await service.create(payload.model_dump())
    return {"incident_id": result["incident_id"], "incident": result["incident"]}


@router.get("/incidents")
async def list_incidents(service: IncidentService = Depends(get_incident_service)) -> dict:
    return {"incidents": service.list()}


@router.get("/incidents/{incident_id}")
async def get_incident(incident_id: str, service: IncidentService = Depends(get_incident_service)) -> dict:
    return service.get(incident_id)


@router.post("/incidents/{incident_id}/run", status_code=202)
async def run_incident(incident_id: str, service: IncidentService = Depends(get_incident_service)) -> dict:
    return await service.run(incident_id)


@router.post("/incidents/{incident_id}/review")
async def review_incident(
    incident_id: str,
    payload: ReviewRequest,
    service: IncidentService = Depends(get_incident_service),
) -> dict:
    review = HITLReview(
        incident_id=incident_id,
        patch_ref=payload.patch_ref or "",
        action=payload.action,
        reason=payload.reason,
        modified_files=payload.modified_files,
    )
    service.review(review)
    return {"incident_id": incident_id, "accepted": True, "action": payload.action.value}


@router.post("/incidents/{incident_id}/interrupt")
async def interrupt_incident(incident_id: str, service: IncidentService = Depends(get_incident_service)) -> dict:
    return await service.interrupt(incident_id)


@router.post("/incidents/{incident_id}/resume", status_code=202)
async def resume_incident(incident_id: str, service: IncidentService = Depends(get_incident_service)) -> dict:
    return await service.resume(incident_id)
