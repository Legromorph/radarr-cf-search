# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

# --- Security & system deps (nur was wirklich nötig ist)
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl tzdata cron dumb-init \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# --- App user
ARG APP_USER=polishrr
RUN useradd -m -u 10001 -s /usr/sbin/nologin ${APP_USER}

WORKDIR /app

# --- Dependencies (no cache)
COPY requirements.txt /app/
RUN python -m pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# --- App files
COPY app.py /app/
COPY web_service.py /app/
COPY entrypoint.sh /app/
COPY cronjob.template /app/
COPY .example_env /app/.example_env

# --- FS hardening
RUN mkdir -p /config /app/runtime && chown -R ${APP_USER}:${APP_USER} /config /app
USER ${APP_USER}

# --- Expose web port
EXPOSE 8998

# --- Healthcheck (nutzt healthz)
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8998/healthz || exit 1

# --- Run: cron im Hintergrund, uvicorn im Vordergrund (PID 1 via dumb-init)
ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/app/entrypoint.sh"]