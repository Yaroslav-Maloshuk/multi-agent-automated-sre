"""BDD steps — structured agent output via the AI gateway."""

import asyncio

from behave import given, then, when
from pydantic import BaseModel

from ai.structured import extract


class Plan(BaseModel):
    owner: str
    plan: list[str]


class RootCause(BaseModel):
    root_cause: str
    confidence: float
    evidence: list[str]


@given("the local deterministic provider is configured")
def step_given_local_provider(context):
    context.model = "local/test"


@when("I ask the planner to plan an incident")
def step_when_plan_incident(context):
    context.plan = asyncio.run(extract(context.model, "planner: plan the incident", Plan))


@then("I receive a plan object")
def step_then_plan_object(context):
    assert isinstance(context.plan, Plan)


@then("the plan has an owner and a list of steps")
def step_then_plan_fields(context):
    assert context.plan.owner
    assert isinstance(context.plan.plan, list)
    assert context.plan.plan


@when("I ask the log analyzer to diagnose an alert")
def step_when_diagnose(context):
    context.root_cause = asyncio.run(extract(context.model, "log_analyzer: diagnose", RootCause))


@then("I receive a root cause object")
def step_then_root_cause(context):
    assert isinstance(context.root_cause, RootCause)


@then("the root cause has a confidence score between 0 and 1")
def step_then_confidence(context):
    assert 0.0 <= context.root_cause.confidence <= 1.0
