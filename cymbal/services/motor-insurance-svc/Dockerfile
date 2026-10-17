FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GODVIEW_ENV=gcp \
    SERVICE_NAME=motor-insurance-svc \
    PORT=8080

WORKDIR /app

RUN groupadd --system appuser && useradd --system --gid appuser --create-home appuser

COPY . /tmp/context/

RUN set -eux; \
    if [ -d /tmp/context/cymbal/services/motor-insurance-svc ]; then \
      cp -a /tmp/context/cymbal/services/motor-insurance-svc/. /app/; \
      cp -a /tmp/context/shared /app/shared; \
    else \
      cp -a /tmp/context/. /app/; \
    fi; \
    rm -rf /tmp/context; \
    pip install --no-cache-dir /app/shared /app; \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
