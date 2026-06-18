FROM python:3.12-slim

WORKDIR /app

# System deps: gcc/libpq for asyncpg+psycopg, ffmpeg for call audio recording.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# Dependency layer
COPY pyproject.toml .

# Source — all three merged modules live under src/
COPY src/ src/
COPY main.py worker.py alembic.ini ./
COPY alembic/ alembic/

RUN uv pip install --system --no-cache -e .

RUN mkdir -p /app/storage /app/recordings /app/media

EXPOSE 8000

# Default: the unified API. The worker overrides this command (see compose).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
