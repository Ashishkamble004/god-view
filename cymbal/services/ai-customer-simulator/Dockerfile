# syntax=docker/dockerfile:1
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

# Create non-root runtime user
RUN groupadd --gid 10001 appuser && \
    useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin appuser

# Copy service source (and staged shared/ package from build_image.sh when present)
COPY . /app/

RUN pip install --no-cache-dir --upgrade pip && \
    if [ -d /app/shared ]; then \
        pip install --no-cache-dir /app/shared /app; \
    else \
        pip install --no-cache-dir /app; \
    fi && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
