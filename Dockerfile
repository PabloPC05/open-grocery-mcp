# Slim multi-arch Dockerfile for Open Grocery MCP on Raspberry Pi
# Supports linux/arm64 (Pi 4/5) and linux/amd64
# This image excludes Playwright/Chromium for minimal resource usage
# For authenticated Carrefour/Eroski, mount storage_state.json via volume

FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy package files
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
COPY data/ data/

# Build wheel with only httpx + mcp (no browser extra)
RUN pip install --no-cache-dir build && \
    python -m build --wheel && \
    pip wheel --no-cache-dir --no-deps --wheel-dir /build/wheels dist/*.whl


FROM python:3.12-slim

# Create non-root user
RUN groupadd -r mcp && useradd -r -g mcp -u 1000 mcp

WORKDIR /app

# Install runtime dependencies (if any)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy wheels and install
COPY --from=builder /build/wheels /tmp/wheels
RUN pip install --no-cache-dir /tmp/wheels/*.whl && \
    rm -rf /tmp/wheels

# Create state directory
RUN mkdir -p /home/mcp/.open-grocery-mcp && \
    chown -R mcp:mcp /home/mcp

USER mcp

# Health check against MCP health endpoint
# The streamable-http transport exposes /health when json_response=True
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()" || exit 1

# Expose HTTP port
EXPOSE 8000

# Environment defaults
ENV OPEN_GROCERY_ENABLE_RETAILER_WRITES=0 \
    OPEN_GROCERY_ENABLE_ORDER_SUBMISSION=0 \
    OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION=0 \
    OPEN_GROCERY_BROWSER_HEADLESS=1 \
    OPEN_GROCERY_STATE_DIR=/home/mcp/.open-grocery-mcp

# Run MCP server with HTTP transport
CMD ["open-grocery-mcp", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
