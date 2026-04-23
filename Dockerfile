FROM nvidia/cuda:12.4-runtime-ubuntu24.04

# Python 3.12 + uv + git (for git-based deps)
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv git curl ca-certificates && \
    curl -LsSf https://astral.sh/uv/install.sh | sh && \
    rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.local/bin:$PATH"

# Create non-root user
RUN useradd -r -m -d /app appuser

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --extra voxtral --extra nats

# Copy source and set ownership
COPY --chown=appuser:appuser . .

# Entrypoint receives mode as argument
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && chown appuser:appuser /entrypoint.sh

USER appuser

ENTRYPOINT ["/entrypoint.sh"]
CMD ["tts"]

# Health check — GPU visibility
HEALTHCHECK CMD nvidia-smi || exit 1
