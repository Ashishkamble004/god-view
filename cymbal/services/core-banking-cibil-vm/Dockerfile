FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GODVIEW_ENV=gcp \
    SERVICE_NAME=core-banking-cibil-vm \
    PORT=8085

WORKDIR /app

RUN groupadd --system appuser && useradd --system --gid appuser --create-home appuser

COPY . /tmp/context/

RUN set -eux; \
    if [ -d /tmp/context/cymbal/services/core-banking-cibil-vm ]; then \
      cp -a /tmp/context/cymbal/services/core-banking-cibil-vm/. /app/; \
      cp -a /tmp/context/shared /app/shared; \
    else \
      cp -a /tmp/context/. /app/; \
    fi; \
    rm -rf /tmp/context; \
    pip install --no-cache-dir /app/shared /app; \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8085

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8085", "--workers", "1"]
