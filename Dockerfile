# Multi-stage build for Open Grocery MCP
# Supports linux/arm64 (Raspberry Pi 4/5) and linux/amd64
# WITHOUT Playwright or Chromium dependencies

FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        && rm -rf /var/lib/apt/lists/*

# Copy package files
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
COPY data/ ./data/

# Build wheel without browser extras
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /build/wheels .

# Final stage
FROM python:3.12-slim

LABEL org.opencontainers.image.title="Open Grocery MCP"
LABEL org.opencontainers.image.description="Resource-light MCP server for supermarket comparison and catalogue search"
LABEL org.opencontainers.image.source="https://github.com/PabloPC05/open-grocery-mcp"
LABEL org.opencontainers.image.licenses="MIT"

WORKDIR /app

# Copy wheels from builder
COPY --from=builder /build/wheels /tmp/wheels
COPY --from=builder /build/pyproject.toml /build/README.md /build/LICENSE ./
COPY --from=builder /build/src ./src
COPY --from=builder /build/data ./data

# Install package without browser extras
# Note: Install the wheel WITHOUT --no-index so pip can fetch httpx/mcp from PyPI
RUN pip install --no-cache-dir /tmp/wheels/open_grocery_mcp-*.whl && \
    rm -rf /tmp/wheels

# Create state directory
RUN mkdir -p /data/.open-grocery-mcp && \
    chmod 700 /data/.open-grocery-mcp

# Set environment defaults
ENV OPEN_GROCERY_STATE_DIR=/data/.open-grocery-mcp \
    OPEN_GROCERY_TRANSPORT=streamable-http \
    OPEN_GROCERY_HOST=0.0.0.0 \
    OPEN_GROCERY_PORT=8000 \
    OPEN_GROCERY_ENABLE_RETAILER_WRITES=0 \
    OPEN_GROCERY_ENABLE_ORDER_SUBMISSION=0 \
    OPEN_GROCERY_ENABLE_BROWSER_ORDER_SUBMISSION=0

# Health check: TCP connection to MCP streamable-http port
# Note: MCP tools are at /mcp, not /health (which exists only on Vercel ASGI)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import socket; s=socket.socket(); s.settimeout(3); s.connect(('127.0.0.1', 8000)); s.close()" || exit 1

EXPOSE 8000

# Run as non-root user
RUN useradd -m -u 1000 grocery && \
    chown -R grocery:grocery /app /data
USER grocery

CMD ["open-grocery-mcp", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
