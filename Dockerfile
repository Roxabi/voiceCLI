FROM nvidia/cuda:12.4-devel-ubuntu24.04

# Python 3.12 + uv + git (for git-based deps)
RUN apt-get update && apt-get install -y python3-venv git curl && \
    curl -LsSf https://astral.sh/uv/install.sh | sh

ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app
COPY . .
RUN uv sync --frozen --extra voxtral --extra nats

# Entrypoint receives mode as argument
COPY deploy/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["tts"]

# Health check — GPU visibility
HEALTHCHECK CMD nvidia-smi || exit 1
