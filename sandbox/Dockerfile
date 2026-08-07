# Agent execution sandbox image.
# Agents never run on the host — this image is what they execute inside:
#   python 3.11 + pytest + flake8, network-isolated, read-only rootfs.
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        pytest>=8.3.0 \
        flake8>=7.0.0

WORKDIR /workspace
CMD ["sleep", "infinity"]
