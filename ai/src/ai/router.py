"""REST + SSE surface of the AI gateway.

Endpoints mirror the OpenAI shape where it makes sense so the SRE agents can
drop in the gateway as their LLM/embedding base URL:

- `POST /v1/chat/completions` (stream via SSE when `stream=true`)
- `POST /v1/embeddings`
- `POST /v1/structured`        (Instructor/Outlines/DSPy constrained JSON)
- `GET  /v1/events/stream`     (server-sent events for long-running jobs)
- `POST /v1/rag/search`        (hybrid: Qdrant + LanceDB + PageIndex, RRF)
- `POST /v1/rag/index`         (ETL trigger)
- `GET  /v1/analytics`         (DuckDB warehouse)
- `POST /v1/eval`              (RAGAS metrics)
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .etl import ETLResult, Warehouse
from .etl import run as run_etl
from .rag import PageIndex
from .routing import available_providers, complete, embed, stream
from .structured import dspy_plan, extract

router = APIRouter()


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage]
    stream: bool = False


class StructuredRequest(BaseModel):
    model: str | None = None
    prompt: str
    json_schema: dict[str, Any] = Field(default_factory=dict, alias="schema")


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5


class IndexRequest(BaseModel):
    urls: list[str] = Field(default_factory=list)


class EvalRequest(BaseModel):
    questions: list[str]
    answers: list[str]
    contexts: list[list[str]]
    ground_truths: list[list[str]] = Field(default_factory=list)


class DspyRequest(BaseModel):
    context: str


@router.get("/health")
async def health() -> dict[str, Any]:
    from .routing import available_providers

    return {"status": "ok", "service": "ai-gateway", "providers": list(available_providers())}


@router.get("/v1/providers")
async def providers() -> dict[str, str]:
    return available_providers()


@router.post("/v1/chat/completions")
async def chat(req: ChatRequest) -> Any:
    model = req.model or _default_model()
    messages = [{"role": m.role, "content": m.content} for m in req.messages]

    if req.stream:
        return _sse_stream(model, messages)

    text = await complete(model, messages)
    return {
        "id": "chatcmpl-local",
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@router.post("/v1/embeddings")
async def embeddings(body: dict[str, Any]) -> dict[str, Any]:
    texts = body.get("input")
    if isinstance(texts, str):
        texts = [texts]
    vectors = await embed(list(texts))
    return {
        "object": "list",
        "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)],
        "model": "sentence-transformers",
    }


@router.post("/v1/structured")
async def structured(req: StructuredRequest) -> dict[str, Any]:
    model = req.model or _default_model()
    schema = req.json_schema or {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    model_cls = _dynamic_model(schema)
    instance = await extract(model, req.prompt, model_cls)
    return {"model": model, "value": instance.model_dump()}


@router.post("/v1/dspy/plan")
async def dspy(req: DspyRequest) -> dict[str, Any]:
    plan = await dspy_plan(req.context)
    return {"schema": "plan", "value": plan}


@router.post("/v1/rag/search")
async def rag_search(req: SearchRequest, background: BackgroundTasks) -> dict[str, Any]:
    index = _get_index()
    hits = await _hybrid_search(index, req.query, req.top_k)
    background.add_task(_log_retrieval, req.query, req.top_k, len(hits))
    return {
        "query": req.query,
        "hits": [{"text": h.text, "score": round(h.score, 4), "source": h.source} for h in hits],
    }


@router.post("/v1/rag/index")
async def rag_index(req: IndexRequest) -> dict[str, ETLResult | int]:
    index = _get_index()
    result = await run_etl(urls=req.urls or None, page_index=index, warehouse=_get_warehouse())
    return {"result": result, "pages": len(index)}


@router.get("/v1/analytics")
async def analytics() -> dict[str, Any]:
    wh = _get_warehouse()
    return {
        "retrieval_recall": wh.retrieval_recall(),
        "chunks": wh.chunk_stats(),
        "recent_retrievals": wh.recent_retrievals(limit=10),
    }


@router.post("/v1/eval")
async def eval_runs(req: EvalRequest) -> dict[str, Any]:
    from .eval import evaluate

    result = await evaluate(req.questions, req.answers, req.contexts, req.ground_truths)
    return {
        "framework": result.framework,
        "samples": result.samples,
        "faithfulness": result.faithfulness,
        "answer_relevancy": result.answer_relevancy,
        "context_recall": result.context_recall,
        "detail": result.detail,
    }


# -- SSE helpers ------------------------------------------------------------

def _sse_stream(model: str, messages: list[dict[str, str]]) -> StreamingResponse:
    async def gen():
        async for token in stream(model, messages):
            chunk = {
                "id": "chatcmpl-sse",
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
        yield f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/v1/events/stream")
async def events_stream(request: Request) -> StreamingResponse:
    async def gen():
        for i in range(5):
            if await request.is_disconnected():
                break
            yield f"event: job\nid: {i}\ndata: {json.dumps({'progress': (i + 1) * 20})}\n\n"
            await asyncio.sleep(0.2)
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# -- wiring -----------------------------------------------------------------

def _default_model() -> str:
    from .config import get_settings

    return get_settings().default_model


def _dynamic_model(schema: dict[str, Any]):
    from pydantic import BaseModel, Field, create_model

    if "properties" not in schema:
        return type("DynamicModel", (BaseModel,), {"ok": Field(default=True, annotation=bool)})
    fields = {}
    for name, spec in schema.get("properties", {}).items():
        ftype = {"string": str, "boolean": bool, "number": float, "integer": int, "array": list}.get(
            spec.get("type", "string"), str
        )
        fields[name] = (ftype, Field(default=spec.get("default")))
    return create_model("DynamicModel", **fields)


_index: PageIndex | None = None


def _get_index() -> PageIndex:
    global _index
    if _index is None:
        from .config import get_settings

        _index = PageIndex(get_settings().sqlite_path)
    return _index


_warehouse: Warehouse | None = None


def _get_warehouse() -> Warehouse:
    global _warehouse
    if _warehouse is None:
        _warehouse = Warehouse()
    return _warehouse


async def _hybrid_search(index: PageIndex, query: str, top_k: int):
    from .config import get_settings
    from .rag import HybridRetriever

    s = get_settings()
    retriever = HybridRetriever(
        page_index=index,
        qdrant_collection=s.qdrant_collection,
        qdrant_url=s.qdrant_url,
        lancedb_path=s.lancedb_path,
    )
    return await retriever.search(query, top_k=top_k)


async def _log_retrieval(query: str, top_k: int, hits: int) -> None:
    _get_warehouse().record_retrieval(query, top_k, hits, "hybrid")
