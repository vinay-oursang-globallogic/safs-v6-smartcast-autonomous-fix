# SAFS v6.0 - Multi-stage Dockerfile
# Builds a production-ready SAFS container with ARM toolchain support.

# ── Stage 1: base ─────────────────────────────────────────────────────────────
FROM python:3.12-slim AS base

LABEL org.opencontainers.image.title="SAFS v6.0"
LABEL org.opencontainers.image.description="SmartCast Autonomous Fix System"
LABEL org.opencontainers.image.vendor="Vizio"
LABEL org.opencontainers.image.version="6.0.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System dependencies: ARM toolchain, QEMU, misc tools
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc-arm-linux-gnueabi \
        binutils-arm-linux-gnueabi \
        gcc-arm-linux-gnueabihf \
        binutils-arm-linux-gnueabihf \
        qemu-user-static \
        git \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid 1001 safs && \
    useradd --uid 1001 --gid 1001 --no-create-home --shell /bin/bash safs

WORKDIR /app

# ── Stage 2: builder ──────────────────────────────────────────────────────────
FROM base AS builder

# Install Python build dependencies
RUN pip install --upgrade pip build wheel

# Copy dependency specs first for layer caching
COPY pyproject.toml ./
COPY README.md ./

# Install runtime dependencies to a prefix
RUN pip install --prefix=/install --no-deps \
    anthropic \
    httpx \
    pydantic \
    pydantic-settings \
    typer \
    rich \
    langgraph \
    langchain-anthropic \
    qdrant-client \
    voyageai \
    drain3 \
    aiohttp \
    asyncpg \
    redis \
    structlog \
    prometheus-client 2>/dev/null || true

# Full install from pyproject.toml
RUN pip install --prefix=/install . 2>/dev/null || \
    pip install --prefix=/install -e . 2>/dev/null || \
    echo "Warning: could not install package, continuing"

# ── Stage 3: production ───────────────────────────────────────────────────────
FROM base AS production

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy source code
COPY --chown=safs:safs src/ /app/src/
COPY --chown=safs:safs pyproject.toml /app/
COPY --chown=safs:safs README.md /app/

# Create directories for runtime artifacts
RUN mkdir -p /app/logs /app/workspace /app/data && \
    chown -R safs:safs /app/logs /app/workspace /app/data

# Switch to non-root user
USER safs

# Health check: verify CLI is accessible
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import safs; print('ok')" 2>/dev/null || exit 1

EXPOSE 8080

# Default: run the Jira webhook listener
ENTRYPOINT ["python", "-m", "safs.cli"]
CMD ["serve-webhook", "--host", "0.0.0.0", "--port", "8080"]

# ── Stage 4: test ─────────────────────────────────────────────────────────────
FROM builder AS test

# Install test dependencies
RUN pip install --prefix=/install \
    pytest \
    pytest-asyncio \
    pytest-cov \
    pytest-mock \
    httpx 2>/dev/null || true

COPY --chown=root:root . /app/
WORKDIR /app

# Run tests
CMD ["/usr/local/bin/python", "-m", "pytest", "tests/", "-q", "--no-header", \
     "--cov=src/safs", "--cov-report=xml", "--cov-report=term-missing"]
