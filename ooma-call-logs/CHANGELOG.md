# Changelog

## 0.1.1

- Fixed a real bug found via a live run: FlareSolverr's own Chromium failed to start ("cannot connect to chrome... chrome not reachable"), because the Dockerfile's `USER root` was never reverted, so the container ran as root -- FlareSolverr's own Dockerfile deliberately ends as a non-root `flaresolverr` user specifically because Chromium crashes ("*** stack smashing detected ***") as root without additional sandboxing setup it doesn't do. Fixed by keeping `USER root` only for the install/copy steps (chowning what was added to `flaresolverr:flaresolverr`, mirroring FlareSolverr's own Dockerfile pattern exactly) and switching back to `USER flaresolverr` before `CMD`.

## 0.1.0

- Initial version: ported from a working Node-RED flow. Bundles FlareSolverr (MIT licensed) in the same container to get past Ooma's Cloudflare protection, logs in natively (direct HTTP, reusing FlareSolverr's cookies) for the POST login and call-logs fetch, parses the call-log HTML table with the same regex-based approach as the original flow (unit-tested against synthetic data matching that structure), and updates `sensor.ooma_call_feed` directly via Home Assistant's REST API (`homeassistant_api: true`, no MQTT broker needed) -- matching the original template sensor's exact attribute shape and entity ID so an existing dashboard card needs no changes. Running FlareSolverr as a background process alongside this add-on's own script in one container is a novel combination not yet verified against a real build; the call-log parser hasn't been tested against real captured HTML. See the README's "First-run debugging" section.
