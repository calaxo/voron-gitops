# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# Test stage: docker build --target test .
FROM base AS test
RUN pip install "pytest>=8" "pytest-asyncio>=0.24" "respx>=0.21"
COPY tests ./tests
RUN python -m pytest -q

# Runtime stage (default)
FROM base AS runtime
RUN useradd --create-home --uid 10001 mcp \
    && mkdir -p /data/gcodes && chown -R mcp:mcp /data
USER mcp
ENV VORON_MCP_DATA_DIR=/data \
    MOONRAKER_URL=http://printer.local:7125 \
    MCP_TRANSPORT=stdio \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000
VOLUME ["/data"]
EXPOSE 8000
ENTRYPOINT ["voron-mcp"]
