#!/usr/bin/env bash
set -Eeuo pipefail

# --- Fix Permissions für /config (Host-Volume)
if [ ! -w /config ]; then
  echo "Fixing /config permissions..."
  chmod -R a+rwX /config 2>/dev/null || true
fi

# --- Defaults
: "${CRON_SCHEDULE:=0 * * * *}"  # jede Stunde
: "${TZ:=Etc/UTC}"
: "${WEB_SERVICE_BIND:=0.0.0.0}"
: "${WEB_SERVICE_PORT:=8998}"
: "${POLISHRR_TOKEN:?POLISHRR_TOKEN env var required}"

echo "Starting Polishrr with schedule '${CRON_SCHEDULE}' and web port ${WEB_SERVICE_PORT}"

# --- Cronjob vorbereiten
if [ ! -f /etc/cron.d/my-cron-job ]; then
  echo "Error: /etc/cron.d/my-cron-job template missing."
  exit 1
fi

mkdir -p /app/runtime
sed "s|\${CRON_SCHEDULE}|$CRON_SCHEDULE|" /etc/cron.d/my-cron-job > /app/runtime/my-cron-job.actual
chmod 0644 /app/runtime/my-cron-job.actual
crontab /app/runtime/my-cron-job.actual
cron
echo "Cron started."

# --- Fix permissions (important for logging) ---
chmod -R 777 /app/runtime

# --- Webservice (non-root) starten
echo "Launching web service as 'polishrr'..."
exec su -s /bin/bash polishrr -c "uvicorn web_service:app \
  --host ${WEB_SERVICE_BIND} \
  --port ${WEB_SERVICE_PORT} \
  --proxy-headers --forwarded-allow-ips='*' \
  --log-level info"
