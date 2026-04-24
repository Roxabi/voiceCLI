# syntax=docker/dockerfile:1
# ── build stage ──────────────────────────────────────────────────────────────
FROM docker.io/nvidia/cuda:12.5.1-runtime-ubuntu24.04 AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONDONTWRITEBYTECODE=1

# uv from official OCI artifact (digest-pinned via manifest, no curl|tar)
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /usr/local/bin/

# Build deps: Python + audio build deps + Cython compiler
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-venv python3-dev \
        gcc g++ \
        portaudio19-dev \
        git ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Layer-cache: install deps before copying source
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra voxtral --extra nats

# Copy source into venv location (no rebuild of deps)
COPY src/ ./src/
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ── runtime stage ─────────────────────────────────────────────────────────────
FROM docker.io/nvidia/cuda:12.5.1-runtime-ubuntu24.04 AS runtime

# Runtime deps only: portaudio shared lib + TLS roots
RUN apt-get update && apt-get install -y --no-install-recommends \
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
    VIRTUAL_ENV="/app/.venv"

WORKDIR /app

USER voicecli

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD voicecli --version || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["tts"]
