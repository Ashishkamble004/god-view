FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SERVICE_NAME=loan-orchestrator-api \
    PORT=8080

WORKDIR /app

RUN groupadd --system appuser && useradd --system --gid appuser --create-home appuser

COPY . /tmp/context/

RUN set -eux; \
    if [ -d /tmp/context/cymbal/services/loan-orchestrator-api ]; then \
      cp -a /tmp/context/cymbal/services/loan-orchestrator-api/. /app/; \
      cp -a /tmp/context/shared /app/shared; \
    else \
      cp -a /tmp/context/. /app/; \
    fi; \
    rm -rf /tmp/context; \
    if [ -d /app/shared ]; then \
      pip install --no-cache-dir /app/shared; \
    fi; \
    pip install --no-cache-dir /app; \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2", "--limit-concurrency", "80"]
