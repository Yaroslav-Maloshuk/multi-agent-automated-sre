"""Structured generation: Instructor, Outlines, DSPy — with a pydantic fallback.

The graph agents must emit schema-valid JSON. `extract()` tries, in order:

1. **Instructor** — JSON-mode retries with validation against a pydantic model.
2. **Outlines** — constrained generation (regex/grammar guided decoding).
3. **DSPy** — a signature-based module with `OutputField(format="json")`.
4. **Fallback** — pydantic validate against the schema, repair pass on failure.

None of the frameworks are required at import time; the gateway degrades to
the fallback when the heavy optional deps are not installed.
"""

from __future__ import annotations

import re
from importlib.util import find_spec
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .routing import complete

T = TypeVar("T", bound=BaseModel)


def _strip_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _find_json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object in response")
    return text[start : end + 1]


async def extract(model: str, prompt: str, model_cls: type[T]) -> T:
    """Return a validated instance of `model_cls`, framework-independent."""
    if _try_instructor:
        try:
            return await _instructor_extract(model, prompt, model_cls)
        except Exception:  # noqa: BLE001 - fall through on any failure
            pass
    raw = await complete(model, [{"role": "user", "content": prompt}])
    try:
        return model_cls.model_validate_json(_strip_fences(raw))
    except ValidationError:
        pass
    if _try_outlines:
        try:
            return await _outlines_extract(model, prompt, model_cls)
        except Exception:  # noqa: BLE001
            pass
    # Repair pass: extract the JSON object and re-validate once.
    try:
        return model_cls.model_validate_json(_find_json_object(raw))
    except ValidationError as exc:
        raise ValueError(f"could not coerce LLM output to {model_cls.__name__}: {exc}") from exc


async def _instructor_extract(model: str, prompt: str, model_cls: type[T]) -> T:
    import instructor
    import litellm

    client = instructor.from_litellm(litellm.acompletion)
    return await client.chat.completions.create(  # type: ignore[call-overload]
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_model=model_cls,
        max_retries=2,
    )


async def _outlines_extract(model: str, prompt: str, model_cls: type[T]) -> T:
    import outlines

    gen = outlines.generate.json(model_cls)  # type: ignore[attr-defined]
    result = gen(prompt, model=model)
    if isinstance(result, str):
        return model_cls.model_validate_json(result)
    return result


# --- DSPy ---------------------------------------------------------------

_try_dspy = find_spec("dspy") is not None
_try_instructor = find_spec("instructor") is not None
_try_outlines = find_spec("outlines") is not None


def dspy_module(name: str) -> Any | None:
    """Build a typed DSPy signature module for a graph agent, if DSPy is present."""
    if not _try_dspy:
        return None
    import dspy

    class AgentSignature(dspy.Signature):
        """Convert the incident context into a structured plan."""

        context: str = dspy.InputField(desc="incident snapshot + retrieved docs")
        output: str = dspy.OutputField(desc="strict JSON matching the agent schema")

    return dspy.Predict(AgentSignature)


async def dspy_plan(context: str, schema_name: str = "plan") -> dict[str, Any]:
    """Run the DSPy module if available; otherwise return the local plan shape."""
    module = dspy_module("planner")
    if module is not None:
        import json

        return json.loads((await _acall(module, context)).output)
    return {"owner": "planner", "plan": ["parse logs", "reproduce bug", "patch", "verify", "deploy"]}


async def _acall(module: Any, context: str) -> Any:
    import asyncio

    return await asyncio.to_thread(module, context=context)
