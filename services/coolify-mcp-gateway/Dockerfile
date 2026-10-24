# GATED: replace this tag with reviewed immutable digest before production.
FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gateway_core.py server.py ./
RUN useradd --uid 10001 --create-home gateway && mkdir -p /data/oauth && chown -R 10001:10001 /data
USER 10001
EXPOSE 8000
CMD ["python", "server.py"]
