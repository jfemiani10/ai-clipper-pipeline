# CUDA 12.2 runtime matches the host driver (535). Using slim cudnn image
# so faster-whisper can use the GPU without installing full CUDA toolkit.
FROM nvidia/cuda:12.2.0-runtime-ubuntu22.04

# Ubuntu 22.04 ships Python 3.10 — fully compatible with this project.
# Install system dependencies including Python and ffmpeg.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN update-alternatives --install /usr/bin/python python /usr/bin/python3 1

WORKDIR /app

# Install Python dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY config/ config/
COPY src/ src/

# Create data and log directories
RUN mkdir -p data/downloads data/transcripts data/clips data/exports logs

# Default command (overridden per-service in docker-compose.yml)
CMD ["python", "src/worker.py"]
