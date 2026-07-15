# Changelog

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
