#!/usr/bin/with-contenv bashio

export OOMA_USERNAME=$(bashio::config 'ooma_username')
export OOMA_PASSWORD=$(bashio::config 'ooma_password')
export FLARESOLVERR_URL=$(bashio::config 'flaresolverr_url')
export RUN_INTERVAL_MINUTES=$(bashio::config 'run_interval_minutes')

bashio::log.info "Starting Ooma Call Logs..."

INTERVAL_SECONDS=$((RUN_INTERVAL_MINUTES * 60))

while true; do
    bashio::log.info "Checking for new call-log entries..."
    python3 -u /app/ooma-call-logs.py || bashio::log.error "Run failed, see traceback above. Will retry next interval."
    bashio::log.info "Sleeping for ${RUN_INTERVAL_MINUTES}m"
    sleep "${INTERVAL_SECONDS}"
done
