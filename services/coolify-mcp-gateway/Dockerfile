# Immutable base image digest recorded by GitHub Actions Docker build on 2026-10-24.
FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gateway_core.py server.py ./
# Seed an empty named volume with an owned file so Docker propagates
# UID 10001 metadata on first mount; the CI volume test validates write access.
RUN useradd --uid 10001 --create-home gateway && mkdir -p /data/oauth && touch /data/oauth/.initialized && chown -R 10001:10001 /data
USER 10001
EXPOSE 8000
# OAuth discovery proves that the HTTP server is serving requests; /mcp itself is protected.
# Coolify's generated curl/wget check cannot run in this minimal image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/.well-known/oauth-authorization-server', timeout=3)"]
CMD ["python", "server.py"]
