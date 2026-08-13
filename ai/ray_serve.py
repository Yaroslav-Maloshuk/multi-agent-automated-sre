"""Ray Serve LLM tier — serves the model via the vLLM engine behind Ray Serve.

Exposes the OpenAI-compatible API (``/v1/chat/completions``, ``/v1/models``) on
:8001, the endpoint the LiteLLM ``ray/qwen`` route points at:

    docker compose --profile gpu-llm up -d ray

Under the hood this is Ray Serve LLM (https://docs.ray.io/en/latest/serve/llm/),
a first-class Ray integration with the vLLM engine: autoscaling, load
balancing, multi-LoRA and a unified OpenAI-compatible ingress.

Run directly on a live cluster:

    python ray_serve.py

or through the Serve CLI (the docker-compose entrypoint):

    serve run ray_serve:entrypoint --port 8001 --host 0.0.0.0
"""

from __future__ import annotations

import os

from ray import serve
from ray.serve.llm import LLMConfig, build_openai_app

MODEL_ID = os.environ.get("HF_MODEL_ID", "Qwen/Qwen2.5-1.5B-Instruct")
TENSOR_PARALLEL = int(os.environ.get("RAY_TENSOR_PARALLEL", "1"))


def entrypoint() -> serve.Application:
    """Application factory consumed by ``serve run ray_serve:entrypoint``."""
    llm_config = LLMConfig(
        model_loading_config={
            "model_id": MODEL_ID,
            "model_source": MODEL_ID,
        },
        deployment_config={
            "autoscaling_config": {
                "min_replicas": 1,
                "max_replicas": 1,
            },
        },
        engine_kwargs={
            "tensor_parallel_size": TENSOR_PARALLEL,
            "gpu_memory_utilization": 0.9,
            "max_model_len": 16384,
            "enable_prefix_caching": True,
        },
    )
    return build_openai_app({"llm_configs": [llm_config]})


if __name__ == "__main__":
    serve.run(entrypoint())
