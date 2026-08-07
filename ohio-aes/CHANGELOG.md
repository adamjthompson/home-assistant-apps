# Changelog

## 0.2.6

- Fixed `page.goto(LOGIN_URL, wait_until="networkidle")` which timed out at 30s on the very first navigation, failing the run before login even started. Replaced every `wait_until="networkidle"`/`wait_for_load_state("networkidle")` in the login/navigation flow with `"load"`. `networkidle` can hang indefinitely on real-world sites with persistent background network activity (analytics, heartbeats, etc.), even once the page has genuinely finished loading. `"load"` is a far more reliable completion signal.

## 0.2.5

- Anchored the daily gap ramp to the average of the 3 real readings on each side of the gap, instead of just the single reading immediately adjacent to it. A lone noisy 15-minute reading (e.g. an AC compressor cycling on right as reporting resumes) could otherwise pull the whole ramp toward an unrepresentative spike, making the last real hour of the gap look inflated relative to the untouched hour right after it.

## 0.2.4

- Switched the daily gap estimate from a smoothstep (ease-in-out) curve to a straight linear ramp. Smoothstep has zero slope at both endpoints, so on days where the real readings right before/after the gap were already fairly flat, the estimate looked flat for the first third of the gap and then rushed through the rise in the middle third -- a fixed artifact of that curve shape, not a data issue. Linear interpolation spreads the rise evenly across the whole gap instead.

## 0.2.3

- Added estimation for a daily reporting gap: AES's export consistently pads a multi-hour mid-day window (9:45am-1:45pm on this account) with a literal `0.00` kWh reading instead of a real value -- a residential meter doesn't actually draw zero for hours at a stretch, and `0.00` doesn't appear anywhere else in the export, so this reads as a placeholder rather than real usage. Runs of 1+ hour of exact-zero 15-minute readings, bounded by real readings on both sides, are now estimated with a smoothstep curve between the last real reading before the gap and the first real reading after it, rather than imported as a false zero-usage dip on the Energy Dashboard. Every estimated fill is logged at warning level with the before/after values it curved between (HA's statistics import has no way to flag an entry as estimated). Gaps over 6 hours are left alone rather than estimated, since something that long is more likely a genuine outage than the daily placeholder.

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
