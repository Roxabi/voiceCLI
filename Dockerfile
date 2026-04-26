# syntax=docker/dockerfile:1
# ── build stage ──────────────────────────────────────────────────────────────
ARG ML_BASE_TAG=cu128-py312-torch2.7.1
FROM ghcr.io/roxabi/ml-base:${ML_BASE_TAG} AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# Build deps: audio build deps only — ml-base already supplies python3.12 + uv + toolchain
RUN apt-get update && apt-get install -y --no-install-recommends \
        portaudio19-dev \
        git ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Layer-cache: install deps before copying source
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev \
        --no-install-package torch \
        --no-install-package torchaudio \
        --extra tts --extra stt --extra nats

# Copy source into venv location (no rebuild of deps)
COPY src/ ./src/
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ── runtime stage ─────────────────────────────────────────────────────────────
FROM docker.io/nvidia/cuda:12.8.1-cudnn9-runtime-ubuntu24.04 AS runtime

# Runtime deps: python3 (ubuntu24.04 default is 3.12) + portaudio shared lib + TLS roots
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        libportaudio2 \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Fixed UID/GID 1501 — distinct from lyra (1500); deterministic on shared hosts
RUN groupadd -r -g 1501 voicecli && \
    useradd -r -u 1501 -g voicecli -m -d /home/voicecli -s /bin/bash voicecli

# Copy venv + source from builder
COPY --from=builder --chown=voicecli:voicecli /app /app
COPY --from=builder /entrypoint.sh /entrypoint.sh

# Ensure venv binaries are on PATH
ENV PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV="/app/.venv" \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

USER voicecli

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD voicecli --version || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["tts"]
