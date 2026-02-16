# OpenCastor Runtime Container
# For GPU acceleration (NVIDIA Jetson/Desktop), swap base image:
# FROM nvcr.io/nvidia/l4t-pytorch:r35.2.1-pth2.0-py3
FROM python:3.10-slim-bullseye

LABEL maintainer="OpenCastor <maintainers@opencastor.com>"
LABEL version="0.1.0-alpha"
LABEL description="The Universal Runtime for Embodied AI."

# System Dependencies
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    git \
    build-essential \
    usbutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV RCAN_SPEC_PATH=/usr/local/share/rcan

# Install Python Dependencies (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Clone the RCAN Standard
RUN git clone https://github.com/continuonai/rcan-spec.git $RCAN_SPEC_PATH

# Copy the OpenCastor Codebase
COPY . .

# Expose the API gateway port
EXPOSE 8000

# Health check -- hit the gateway's /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1

# Default: run the API gateway (includes brain + channels)
CMD ["python", "-m", "castor.api", "--host", "0.0.0.0", "--port", "8000"]
