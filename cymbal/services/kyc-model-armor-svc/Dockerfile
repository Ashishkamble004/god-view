# Cymbal Auto Finance Utility 1 — kyc-model-armor-svc
# GKE Autopilot pod sizing: 0.25 vCPU (250m) / 512 MiB, non-root, port 8080
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    SERVICE_NAME=kyc-model-armor-svc \
    MODEL_ARMOR_TEMPLATE_ID=projects/general-ak/locations/us-central1/templates/cymbal-text-guard

WORKDIR /app

RUN groupadd --gid 10001 appgroup && \
    useradd --uid 10001 --gid appgroup --shell /sbin/nologin --create-home appuser

COPY . /app

# Install godview-shared (godview_shared) when staged into ./shared by build_image.sh, then install app
RUN if [ -d /app/shared ]; then pip install --no-cache-dir /app/shared; fi && \
    pip install --no-cache-dir /app && \
    chown -R appuser:appgroup /app

USER appuser

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
