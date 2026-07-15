# Changelog

## 0.2.2

- Removed the on-failure debug screenshot/HTML dump (`/share/ohio_aes_debug.png`, `/share/ohio_aes_debug.html`). It was only ever needed to map selectors against the live site during initial development; now that the automation flow is confirmed stable, a failed run just logs and re-raises the exception.

## 0.2.0

- Replaced MQTT publishing with direct Home Assistant long-term statistics import: MQTT state changes are timestamped at message-arrival time regardless of any date/hour embedded in the payload, so usage was showing up on charts dated to whenever the app happened to run rather than the day/hour it actually occurred (compounded by AES's 1-2 day data-finalization lag). Hourly totals are now imported via `recorder/import_statistics` as an external statistic (`ohio_aes:hourly_usage`), correctly dated and ready to add as an Energy Dashboard source. This removes the MQTT broker dependency and all `mqtt_*` config options entirely, and adds `homeassistant_api: true` so the app can call HA's own API (no manual token needed). Breaking change for anyone who had wired the old `mqtt_topic`/`mqtt_topic_hourly` topics into their own dashboards or automations.
- The statistics import keeps a running cumulative total in `/share/ohio_aes_state.json` (Home Assistant's Energy Dashboard needs a monotonically increasing `sum`, not each hour's discrete kWh). If `days_back` is increased, the newly-exposed older hours are deliberately skipped rather than imported, since there's no reliable prior baseline to build their cumulative total on without corrupting every already-imported hour after them -- a logged warning notes when this happens.

## 0.1.9

- Added MQTT Discovery: the app now publishes retained discovery config for both the daily and hourly usage sensors on every run, so Home Assistant creates them automatically under an "AES Ohio Energy Usage" device instead of requiring the user to hand-write sensor YAML.

## 0.1.8

- Added an hourly usage sensor: the 15-minute interval rows in the export are now also bucketed and summed per hour, with the most recent complete hour published (retained) to a new `mqtt_topic_hourly` topic alongside the existing daily total.

## 0.1.3

- Fixed flaky "PowerView" click: the "My Usage" dropdown is CSS hover-only and was closing mid-click before Playwright could reach the link, so the automation now opens the known Opower dashboard URL directly in a new tab of the same authenticated browser context instead of hovering/clicking through the nav.

## 0.1.2

- Fixed post-login navigation: the real AES Ohio dashboard has no "Your Energy Use" link. "My Usage" is a hover-revealed dropdown trigger, and the actual Opower link is "PowerView", which opens in a new tab -- updated the automation to hover the trigger and capture the popup page instead of waiting on the original page to navigate.

## 0.1.1

- Fixed build failure: switched base image from Alpine to Debian (via build.yaml) because Playwright's pip package has no musllinux/Alpine wheel for any version, so it could never install on the Alpine base regardless of pinned version.

## 0.1.0

- Initial release: Playwright-driven login through AES Ohio's SAML/OIDC chain to Opower, CSV interval export download, daily total published to MQTT.
