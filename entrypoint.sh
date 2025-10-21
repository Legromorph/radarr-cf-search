#!/usr/bin/env bash
set -Eeuo pipefail

# --- defaults ---
: "${CRON_SCHEDULE:=0 * * * *}"          # Default: hourly
: "${TZ:=Etc/UTC}"
: "${WEB_SERVICE_BIND:=0.0.0.0}"
: "${WEB_SERVICE_PORT:=8998}"
: "${POLISHRR_TOKEN:?POLISHRR_TOKEN env var required}"

echo "Starting Polishrr with schedule '${CRON_SCHEDULE}' and web port ${WEB_SERVICE_PORT}"

# --- Prepare cron job ---
if [ ! -f /etc/cron.d/my-cron-job ]; then
  echo "Error: /etc/cron.d/my-cron-job template missing."
  exit 1
fi

# Replace placeholder with the environment variable
sed "s|\${CRON_SCHEDULE}|$CRON_SCHEDULE|" /etc/cron.d/my-cron-job > /etc/cron.d/my-cron-job.actual
chmod 0644 /etc/cron.d/my-cron-job.actual
crontab /etc/cron.d/my-cron-job.actual

# Start cron in background (daemon mode)
cron

# --- Start web service in foreground ---
# Ensure .env is loaded from /config for python
export PYTHONPATH=/app

# Show current env setup (for debugging)
echo "Environment: TZ=$TZ, PORT=$WEB_SERVICE_PORT, CRON=$CRON_SCHEDULE"

# Start the FastAPI server
exec uvicorn web_service:app \
  --host "${WEB_SERVICE_BIND}" \
  --port "${WEB_SERVICE_PORT}" \
  --proxy-headers --forwarded-allow-ips="*" \
  --log-level info