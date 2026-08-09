"""Envelope schema contract."""
from __future__ import annotations

from app.bus.schemas import (
    Channel,
    EventEnvelope,
    EventType,
    HITLReview,
    ReviewAction,
    WSClientMessage,
)


def test_envelope_defaults() -> None:
    envelope = EventEnvelope(type=EventType.AGENT_LOG, channel=Channel.incident("inc_1"))
    assert envelope.version == 1
    assert envelope.id.startswith("evt_")
    assert envelope.payload == {}


def test_envelope_roundtrip_bytes() -> None:
    envelope = EventEnvelope(
        type=EventType.PENDING_APPROVAL,
        channel=Channel.incident("inc_1"),
        incident_id="inc_1",
        run_id="run_1",
        payload={"patch_ref": "patch_1"},
    )
    parsed = EventEnvelope.parse(envelope.to_bytes())
    assert parsed == envelope
    assert parsed.type is EventType.PENDING_APPROVAL


def test_client_message_validation() -> None:
    message = WSClientMessage.model_validate(
        {"action": "hitl.review", "incident_id": "inc_1", "payload": {"action": "approve"}}
    )
    assert message.action.value == "hitl.review"


def test_hitl_review_modify_payload() -> None:
    review = HITLReview(
        incident_id="inc_1",
        patch_ref="patch_1",
        action=ReviewAction.MODIFY,
        modified_files={"src/a.py": "fixed"},
    )
    assert review.to_event_payload()["modified_files"]["src/a.py"] == "fixed"
