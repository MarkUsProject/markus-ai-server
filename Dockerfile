# Dockerfile based on https://github.com/astral-sh/uv-docker-example/blob/main/Dockerfile
# Use a Python image with uv pre-installed
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim

# Setup a non-root user
RUN groupadd --system --gid 1001 nonroot \
 && useradd --system --gid 1001 --uid 1001 --create-home nonroot

# Install Ollama, and bake the default model into the image so it's
# available without a runtime download
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates zstd \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSL https://ollama.com/install.sh | sh
RUN ollama serve & \
    OLLAMA_PID=$! && \
    until curl -sf http://127.0.0.1:11434 >/dev/null; do sleep 1; done && \
    ollama pull smollm2:135m-instruct-q2_K && \
    kill "$OLLAMA_PID"

# Install the project into `/app`
WORKDIR /app

# Keeps Python from buffering stdout and stderr to avoid situations where
# the application crashes without emitting any logs due to buffering.
ENV PYTHONUNBUFFERED=1

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy from the cache instead of linking since it's a mounted volume
ENV UV_LINK_MODE=copy

# Omit development dependencies
ENV UV_NO_DEV=1

# Ensure installed tools can be executed out of the box
ENV UV_TOOL_BIN_DIR=/usr/local/bin

# Install the project's dependencies using the lockfile and settings
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

# Then, add the rest of the project source code and install it
# Installing separately from its dependencies allows optimal layer caching
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

# Reset the entrypoint, don't invoke `uv`
ENTRYPOINT []

# Use the non-root user to run our application
USER nonroot

# Run Ollama alongside the Flask application by default
# Uses `uv run` to sync dependencies on startup, respecting UV_NO_DEV
CMD ["sh", "-c", "ollama serve & exec uv run python -m markus_ai_server"]
