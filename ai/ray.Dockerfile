# Ray Serve LLM tier — the model is served behind Ray Serve via the vLLM
# engine (autoscaling, load balancing, OpenAI-compatible ingress).
# Used only by the `gpu-llm` profile:  docker compose --profile gpu-llm up -d ray
FROM rayproject/ray:latest-py312

USER root

# Ray Serve LLM drives the vLLM engine under the hood.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir vllm

COPY ray_serve.py /home/ray/ray_serve.py

USER ray
WORKDIR /home/ray

# 8001 — OpenAI-compatible ingress; 8265 — Ray dashboard.
EXPOSE 8001 8265

CMD ["serve", "run", "ray_serve:entrypoint", "--port", "8001", "--host", "0.0.0.0"]
