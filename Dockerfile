FROM python:3.12-slim

# Install FFmpeg and system utilities required for video/audio processing & rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy agent source code
COPY . .

# Create persistent data directories
RUN mkdir -p /app/data/incoming /app/data/processed /app/data/thumbnails \
    /app/data/generated /app/data/assets/sfx /app/data/assets/luts \
    /app/data/assets/overlays /app/data/assets/backgrounds

# Cloud Run sets PORT=8080 by default
ENV PORT=8080
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
