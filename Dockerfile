# OpenCastor — The Universal Runtime for Embodied AI
# Multi-stage build for x86_64 and arm64
# For GPU acceleration (NVIDIA Jetson/Desktop), swap base image accordingly.

# ── Stage 1: Builder ─────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install deps first (layer caching)
COPY pyproject.toml requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Copy source and install package
COPY . .
RUN pip install --no-cache-dir --prefix=/install .

# ── Stage 2: Runtime ─────────────────────────────────────────────
FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="OpenCastor"
LABEL org.opencontainers.image.description="The Universal Runtime for Embodied AI"
LABEL org.opencontainers.image.source="https://github.com/craigm26/OpenCastor"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.vendor="OpenCastor Contributors"

# Runtime system deps only (no build-essential)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx \
    libglib2.0-0 \
    usbutils \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --system castor && \
    useradd --system --gid castor --no-create-home --shell /usr/sbin/nologin castor

WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy source (for streamlit dashboard, configs, etc.)
COPY --from=builder /build/castor ./castor
COPY --from=builder /build/pyproject.toml ./

# Create config mount point
RUN mkdir -p /app/config && chown castor:castor /app/config

# Switch to non-root user
USER castor

# Expose API + Dashboard ports
EXPOSE 8000 8501

# Health check — hit the gateway's /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1

# Default: run with config from volume mount
ENTRYPOINT ["castor"]
CMD ["run", "--config", "/app/config/robot.rcan.yaml"]
