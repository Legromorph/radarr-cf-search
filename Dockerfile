# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

# --- Minimalpakete installieren
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl tzdata cron dumb-init \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# --- Non-root App-User
ARG APP_USER=polishrr
RUN useradd -m -u 1000 -s /usr/sbin/nologin ${APP_USER}

# --- Verzeichnisse vorbereiten
RUN mkdir -p /config /app/runtime /var/run /run && \
    chown -R 1000:1000 /config /app /var/run /run && \
    chmod -R 777 /config /app/runtime  # Schreibrechte für Logfiles

WORKDIR /app

# --- Dependencies installieren
COPY requirements.txt /app/
RUN python -m pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# --- App-Dateien + Static Assets
COPY app.py web_service.py entrypoint.sh cronjob.template .example_env /app/
COPY static/ /app/static/
COPY assets/ /app/assets/

# --- Root-Aktionen: Cron + CRLF-Fix
USER root
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh && \
    cp /app/cronjob.template /etc/cron.d/my-cron-job && \
    chmod 0644 /etc/cron.d/my-cron-job

# --- Expose Web Port
EXPOSE 8998

# --- Healthcheck
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8998/healthz || exit 1

# --- Start: Cron (root) + Webservice (als polishrr)
ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/app/entrypoint.sh"]
