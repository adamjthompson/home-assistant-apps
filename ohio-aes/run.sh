#!/usr/bin/with-contenv bashio

export AES_USERNAME=$(bashio::config 'aes_username')
export AES_PASSWORD=$(bashio::config 'aes_password')
export DAYS_BACK=$(bashio::config 'days_back')
export RUN_INTERVAL_HOURS=$(bashio::config 'run_interval_hours')

bashio::log.info "Starting AES Ohio Energy Usage..."

INTERVAL_SECONDS=$((RUN_INTERVAL_HOURS * 3600))

while true; do
    bashio::log.info "Running scrape..."
    python3 -u /app/ohio-aes.py || bashio::log.error "Run failed, see traceback above. Will retry next interval."
    bashio::log.info "Sleeping for ${RUN_INTERVAL_HOURS}h"
    sleep "${INTERVAL_SECONDS}"
done
