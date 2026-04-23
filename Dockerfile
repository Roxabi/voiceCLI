FROM nvidia/cuda:12.4-runtime-ubuntu24.04

# Install uv with pinned version (avoid curl | sh supply chain risk)
ENV UV_VERSION=0.6.17
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-venv git curl ca-certificates && \
    curl -fsSL "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz" | \
    tar -xzf - -C /usr/local/bin uv && \
    rm -rf /var/lib/apt/lists/* && \
    chmod +x /usr/local/bin/uv

# Create non-root user with home directory
RUN useradd -r -m -d /home/appuser -s /bin/bash appuser

WORKDIR /app

# Copy dependency files first for layer caching
COPY --chown=appuser:appuser pyproject.toml uv.lock ./
RUN uv sync --frozen --extra voxtral --extra nats

# Copy source and set ownership
COPY --chown=appuser:appuser . .

# Entrypoint
COPY --chown=appuser:appuser deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER appuser

ENTRYPOINT ["/entrypoint.sh"]
CMD ["tts"]

# Health check — GPU visibility + process check
HEALTHCHECK CMD nvidia-smi && pgrep -f "voicecli nats-serve" || exit 1
