# Dockerfile for Utility 2: underwriting-los-svc
# Sized for GKE Autopilot 0.25 vCPU (250m) / 512 MiB pods in namespace `cymbal`
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

# Create non-root user and group
RUN groupadd --gid 10001 appgroup && \
    useradd --uid 10001 --gid appgroup --shell /sbin/nologin --create-home appuser

# Copy service source (and shared package if staged by infra/deploy/build_image.sh)
COPY . /app

# Install godview-shared (godview_shared) if staged alongside service, then install service
RUN pip install --no-cache-dir --upgrade pip && \
    if [ -d /app/shared ]; then pip install --no-cache-dir /app/shared; fi && \
    pip install --no-cache-dir /app && \
    chown -R appuser:appgroup /app

USER 10001:10001

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
