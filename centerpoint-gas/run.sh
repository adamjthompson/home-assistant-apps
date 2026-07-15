#!/usr/bin/with-contenv bashio

export CENTERPOINT_USERNAME=$(bashio::config 'centerpoint_username')
export CENTERPOINT_PASSWORD=$(bashio::config 'centerpoint_password')
export CENTERPOINT_METER_NUMBER=$(bashio::config 'centerpoint_meter_number')
export CENTERPOINT_INSTALLATION_ID=$(bashio::config 'centerpoint_installation_id')
export GMAIL_ADDRESS=$(bashio::config 'gmail_address')
export GMAIL_APP_PASSWORD=$(bashio::config 'gmail_app_password')
export CYCLES_BACK=$(bashio::config 'cycles_back')
export RUN_INTERVAL_HOURS=$(bashio::config 'run_interval_hours')

bashio::log.info "Starting CenterPoint Energy Gas Usage..."

INTERVAL_SECONDS=$((RUN_INTERVAL_HOURS * 3600))

while true; do
    bashio::log.info "Checking for new billing-history rows..."
    python3 -u /app/centerpoint-gas.py || bashio::log.error "Run failed, see traceback above. Will retry next interval."
    bashio::log.info "Sleeping for ${RUN_INTERVAL_HOURS}h"
    sleep "${INTERVAL_SECONDS}"
done
