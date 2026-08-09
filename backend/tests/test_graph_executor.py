"""Graph executor end-to-end: full incident resolution, telemetry, HITL."""
from __future__ import annotations

import asyncio
from collections import deque

from app.bus.schemas import EventType, HITLReview, ReviewAction
from app.graph.state import GraphStatus
from tests.conftest import build_executor, wait_for_event
from tests.fakes import BROKEN_SERVICE, GOOD_SERVICE, INCIDENT, FakeLLM


async def test_full_run_reaches_completed(bus, memory, pool, settings, events) -> None:
    llm = FakeLLM()
    executor = build_executor(bus, memory, pool, settings, llm)

    state = await executor.run(INCIDENT)

    assert state.status is GraphStatus.COMPLETED
    assert state.root_cause is not None
    assert len(state.subtasks) == 3
    assert state.patch is not None and state.patch.status.value == "APPLIED"
    assert state.verification is not None and state.verification.ok
    assert state.token_usage.output_tokens > 0
    assert state.token_usage.input_tokens > 0
    assert state.hitl_approved is True
    assert state.step_index == 7  # one step per node

    await wait_for_event(events, EventType.GRAPH_STARTED)
    await wait_for_event(events, EventType.GRAPH_COMPLETED)
    await wait_for_event(events, EventType.PATCH_GENERATED)
    await wait_for_event(events, EventType.PATCH_APPLIED)
    await wait_for_event(events, EventType.REVIEW_DECIDED)


async def test_hitl_blocks_then_approve_resumes(bus, memory, pool, settings, events) -> None:
    settings = settings.model_copy(update={"graph_hitl_auto_approve": False})
    llm = FakeLLM()
    executor = build_executor(bus, memory, pool, settings, llm)

    task = asyncio.create_task(executor.run(INCIDENT))
    pending = await wait_for_event(events, EventType.PENDING_APPROVAL)
    assert pending.incident_id == "inc_test"
    assert not task.done()

    executor.submit_review(
        HITLReview(incident_id="inc_test", run_id="", patch_ref=pending.payload["patch_ref"], action=ReviewAction.APPROVE, reason="looks good")
    )
    state = await asyncio.wait_for(task, timeout=20)
    assert state.status is GraphStatus.COMPLETED
    assert state.patch is not None and state.patch.status.value == "APPLIED"


async def test_hitl_reject_cancels_run(bus, memory, pool, settings, events) -> None:
    settings = settings.model_copy(update={"graph_hitl_auto_approve": False})
    llm = FakeLLM()
    executor = build_executor(bus, memory, pool, settings, llm)

    task = asyncio.create_task(executor.run(INCIDENT))
    await wait_for_event(events, EventType.PENDING_APPROVAL)

    executor.submit_review(
        HITLReview(incident_id="inc_test", run_id="", patch_ref="patch_x", action=ReviewAction.REJECT, reason="nope")
    )
    state = await asyncio.wait_for(task, timeout=20)
    assert state.status is GraphStatus.CANCELLED
    assert state.patch is not None and state.patch.status.value == "REJECTED"


async def test_hitl_modify_updates_patch(bus, memory, pool, settings, events) -> None:
    settings = settings.model_copy(update={"graph_hitl_auto_approve": False})
    llm = FakeLLM()
    executor = build_executor(bus, memory, pool, settings, llm)

    task = asyncio.create_task(executor.run(INCIDENT))
    pending = await wait_for_event(events, EventType.PENDING_APPROVAL)

    executor.submit_review(
        HITLReview(
            incident_id="inc_test",
            run_id="",
            patch_ref=pending.payload["patch_ref"],
            action=ReviewAction.MODIFY,
            modified_files={"service.py": GOOD_SERVICE + "# operator tweak\n"},
        )
    )
    state = await asyncio.wait_for(task, timeout=20)
    assert state.status is GraphStatus.COMPLETED
    assert state.patch is not None
    assert "# operator tweak" in state.patch.files[0].new_content


async def test_verifier_self_correction_loop(bus, memory, pool, settings) -> None:
    llm = FakeLLM(fix_contents=deque([BROKEN_SERVICE, BROKEN_SERVICE, GOOD_SERVICE]))
    executor = build_executor(bus, memory, pool, settings, llm)

    state = await executor.run(INCIDENT)

    assert state.verifier_attempt == 3
    assert state.status is GraphStatus.COMPLETED
    assert state.verification is not None and state.verification.ok


async def test_verifier_exhaustion_fails_run(bus, memory, pool, settings) -> None:
    llm = FakeLLM(fix_contents=deque([BROKEN_SERVICE] * 10))
    executor = build_executor(bus, memory, pool, settings, llm)

    state = await executor.run(INCIDENT)

    assert state.verifier_attempt == settings.graph_max_verifier_attempts
    assert state.status is GraphStatus.FAILED
    assert "verification failed" in (state.error or "")


async def test_interrupt_during_approval(bus, memory, pool, settings, events) -> None:
    settings = settings.model_copy(update={"graph_hitl_auto_approve": False})
    llm = FakeLLM()
    executor = build_executor(bus, memory, pool, settings, llm)

    asyncio.create_task(executor.run(INCIDENT))
    await wait_for_event(events, EventType.PENDING_APPROVAL)

    state = await executor.interrupt("inc_test")
    assert state.status is GraphStatus.INTERRUPTED
