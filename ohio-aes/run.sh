#!/usr/bin/with-contenv bashio

export AES_USERNAME=$(bashio::config 'aes_username')
export AES_PASSWORD=$(bashio::config 'aes_password')
export MQTT_HOST=$(bashio::config 'mqtt_host')
export MQTT_PORT=$(bashio::config 'mqtt_port')
export MQTT_USER=$(bashio::config 'mqtt_user')
export MQTT_PASS=$(bashio::config 'mqtt_pass')
export MQTT_TOPIC=$(bashio::config 'mqtt_topic')
export DAYS_BACK=$(bashio::config 'days_back')
export RUN_INTERVAL_HOURS=$(bashio::config 'run_interval_hours')

bashio::log.info "Starting AES Ohio Energy Usage..."
bashio::log.info "MQTT: ${MQTT_HOST}:${MQTT_PORT} -> ${MQTT_TOPIC}"

INTERVAL_SECONDS=$((RUN_INTERVAL_HOURS * 3600))

while true; do
    bashio::log.info "Running scrape..."
    python3 -u /app/ohio-aes.py || bashio::log.error "Run failed, see traceback above. Will retry next interval."
    bashio::log.info "Sleeping for ${RUN_INTERVAL_HOURS}h"
    sleep "${INTERVAL_SECONDS}"
done
