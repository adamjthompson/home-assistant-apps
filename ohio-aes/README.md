# AES Ohio Energy Usage — Home Assistant App

Logs into your AES Ohio account (`myprofile.aes-ohio.com`), downloads your
electricity usage, and publishes both a daily and an hourly total to Home
Assistant via MQTT.

## How it works

AES Ohio's usage portal turns out to be backed by Opower
(`aeso.opower.com`), reached through a real SAML + OAuth2 federation chain:

```
myprofile.aes-ohio.com (login)
        -> SAML assertion
Oracle Identity Cloud Service (SSO broker)
        -> OAuth2 authorization code
aeso.opower.com (usage data + "Download my data" export)
```

Every hop issues a short-lived, dynamically-generated token, so this app
drives a real headless Chromium (via Playwright) through the whole login
and export flow rather than trying to hand-replicate the redirects. Once on
the usage page, it uses the built-in **Download my data** export (the same
"Green Button" feature available in the portal UI) to get 15-minute
interval usage as CSV, and sums it into both a daily total and an hourly
total, publishing the most recent of each to MQTT.

## Installation

1. In Home Assistant, go to **Settings → Apps → Install App**
2. Click the **⋮** menu → **Repositories**
3. Add this repository URL
4. Find **AES Ohio Energy Usage** and click **Install**

## Configuration

```yaml
aes_username: "your-aes-ohio-username"
aes_password: "your-aes-ohio-password"
mqtt_host: "core-mosquitto"
mqtt_port: 1883
mqtt_user: ""
mqtt_pass: ""
mqtt_topic: "homeassistant/aes/usage"
mqtt_topic_hourly: "homeassistant/aes/usage_hourly"
days_back: 3
run_interval_hours: 12
```

`days_back` controls how many days of usage history are requested on each
run (a few days of overlap covers AES's data-finalization lag); the app
always publishes the most recent day (and most recent hour) it received.
`run_interval_hours` controls how often it logs in and re-checks.

## MQTT payload and sensors

Each run publishes retained MQTT Discovery config to
`homeassistant/sensor/aes_ohio_{daily,hourly}_usage/config`, so Home
Assistant automatically creates two sensors under an "AES Ohio Energy Usage"
device -- no manual `configuration.yaml` sensor setup needed:

- **`sensor.aes_ohio_daily_usage`** -- state topic `mqtt_topic`:
  ```json
  { "date": "2026-07-14", "kwh": 21.436 }
  ```
- **`sensor.aes_ohio_hourly_usage`** -- state topic `mqtt_topic_hourly`, for
  the most recent complete hour in the export:
  ```json
  { "hour": "2026-07-14T13:00:00", "kwh": 1.436 }
  ```

Discovery assumes the default `homeassistant` discovery prefix; if you've
customized your MQTT integration's discovery prefix, the sensors won't be
picked up automatically and you'd need to publish equivalent sensor YAML
yourself using the state topics above.

## First-run debugging

The login form fields are confirmed against the live site, but the
"Your Energy Use" navigation link and the "Download my data" modal
(date fields, CSV selection, Export button) were mapped from screenshots,
not live selector inspection — they're the most likely spot to need a
small tweak. If a run fails, the app saves a full-page screenshot and the
page's HTML to the Home Assistant `share` folder:

- `/share/ohio_aes_debug.png`
- `/share/ohio_aes_debug.html`

Check the app's log for where it failed, look at the screenshot, and adjust
the corresponding `page.get_by_...` selector in `ohio-aes.py`.

## Notes

- This app is not affiliated with AES Ohio, Opower, or Oracle.
- Only tested against a single-meter residential account.
