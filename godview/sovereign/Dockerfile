FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/shared \
    PORT=8080 \
    GODVIEW_ENV=gcp

# Install Node.js 22 and curl/ca-certificates for the 4 official MCP stdio servers (Decision A3)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @google-cloud/observability-mcp \
    && rm -rf /var/lib/apt/lists/*

# Install MCP stdio binary wrappers for gke-mcp, genai-toolbox (cloud-sql-postgres), and github-mcp-server
RUN printf '#!/bin/sh\nexec npx -y @google-cloud/gke-mcp "$@"\n' > /usr/local/bin/gke-mcp \
    && printf '#!/bin/sh\nexec npx -y @googleapis/genai-toolbox "$@"\n' > /usr/local/bin/genai-toolbox \
    && printf '#!/bin/sh\nexec npx -y @modelcontextprotocol/server-github "$@"\n' > /usr/local/bin/github-mcp-server \
    && chmod +x /usr/local/bin/gke-mcp /usr/local/bin/genai-toolbox /usr/local/bin/github-mcp-server

WORKDIR /app

COPY shared /app/shared
COPY infra /app/infra
COPY godview/__init__.py /app/godview/__init__.py
COPY godview/cli.py /app/godview/cli.py
COPY godview/ledger /app/godview/ledger
COPY godview/invariants /app/godview/invariants
COPY godview/adapters /app/godview/adapters
COPY godview/harness /app/godview/harness
COPY godview/agents /app/godview/agents
COPY godview/sovereign /app/godview/sovereign

RUN rm -rf /app/godview/god-view-sre-web \
    && pip install --no-cache-dir \
        google-cloud-firestore \
        google-cloud-bigquery \
        google-cloud-pubsub \
        "google-cloud-aiplatform[agent_engines,adk]" \
        google-adk \
        a2a-sdk \
        google-genai \
        google-auth \
        requests \
        PyJWT \
        cryptography \
        /app/shared \
        /app/godview/sovereign \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

CMD ["uvicorn", "godview.sovereign.control_api:app", "--host", "0.0.0.0", "--port", "8080"]
