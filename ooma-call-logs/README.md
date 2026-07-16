# Ooma Call Logs — Home Assistant App

Logs into your Ooma account, reads your recent call log, and updates a
Home Assistant sensor directly -- no separate Node-RED flow, no
separately-run Cloudflare-bypass proxy, no hand-written MQTT template
sensor. Ported from a working Node-RED flow; see "How it works" below for
what changed and why.

## How it works

Ooma's customer portal (`my.ooma.com`) sits behind Cloudflare, so a plain
HTTP client (or even a plain headless browser) gets blocked before ever
reaching the login page. This add-on bundles
[FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) (MIT licensed)
-- a proxy that drives a specially-patched Chromium to solve Cloudflare's
challenge -- in the same container, rather than requiring it as a separate
service you'd have to stand up yourself.

Only the *first* step (fetching the login page) goes through FlareSolverr:

1. FlareSolverr fetches `https://my.ooma.com/login` and returns the page's
   HTML plus the Cloudflare-clearance cookies it picked up along the way.
2. The CSRF token is extracted from that HTML, and a plain (non-FlareSolverr)
   HTTP POST submits the login form directly, reusing those cookies --
   Cloudflare's clearance is tied to the cookies/session, not to every
   individual request needing to go through a real browser again.
3. Login success is confirmed by finding Ooma's own `_myooma2_session`
   cookie in the login response.
4. The call-logs page is fetched directly (same cookies), and its HTML
   table is parsed with the same regex-based approach the original flow
   used (matching on cell markup like `icon-down`/`icon-up` classes for
   call type, not just visible text).
5. The parsed call list is written directly to a `sensor.ooma_call_feed`
   entity via Home Assistant's own REST API (`homeassistant_api: true`) --
   no MQTT broker required.

The sensor's shape deliberately matches the user's original MQTT-triggered
template sensor exactly (`state` = last-updated timestamp, `attributes.calls`
/`count`/`status`) and reuses the same entity ID, so an existing dashboard
card built against that sensor keeps working with zero changes.

## Installation

1. In Home Assistant, go to **Settings → Apps → Install App**
2. Click the **⋮** menu → **Repositories**
3. Add this repository URL
4. Find **Ooma Call Logs** and click **Install**

This app requests `homeassistant_api: true`, which lets Supervisor hand it a
scoped token to call Home Assistant's own API -- no manual long-lived access
token or MQTT broker needed.

## Configuration

```yaml
ooma_username: "your-ooma-username"
ooma_password: "your-ooma-password"
run_interval_minutes: 15
```

- `run_interval_minutes` defaults to match the original flow's 15-minute
  cadence, but each run destroys and recreates a FlareSolverr session --
  meaning a full patched-Chromium instance spins up every run. That's a
  real, ongoing CPU/RAM cost (roughly 300-500MB+ per session), worth tuning
  down on constrained hardware (e.g. a Raspberry Pi) if 15 minutes turns out
  to be too frequent for your setup.

## Home Assistant sensor

Each run updates `sensor.ooma_call_feed` directly:

- `state`: a "last updated" timestamp (the real data lives in attributes,
  matching the original template sensor's convention)
- `attributes.calls`: list of `{type, number, name, date, duration}`
- `attributes.count`: number of calls found
- `attributes.status`: `"success"` / `"empty_table"` / `"error"`

If you're migrating from the original Node-RED flow + template sensor, no
dashboard changes are needed -- just remove the old template sensor YAML and
Node-RED flow once this add-on is confirmed working; your existing card
(entity ID `sensor.ooma_call_feed`) keeps working as-is.

## First-run debugging

**This add-on has real, unverified pieces** -- ported and unit-tested
against synthetic data matching the original flow's logic, but not yet run
against the live site or a real container build:

- **FlareSolverr running as a background process alongside this add-on's
  own script, in one container, has not been tested.** FlareSolverr is
  normally one process per container; this bundles it with a second
  process via `start.sh`. If the container fails to start or FlareSolverr
  never reports ready, check the log for `start.sh`'s output first --
  the fallback, if this specific combination turns out not to work
  cleanly, is running FlareSolverr as its own lightweight sidecar process
  still launched from this same add-on (still one installable unit from an
  end user's perspective), rather than assuming this exact packaging.
  Health is checked via FlareSolverr's real `GET /health` endpoint.
- The regex-based call-log table parser (`parse_call_logs_html`) is a
  direct, unit-tested port of the original flow's logic, but has only been
  tested against a synthetic HTML sample built to match the original flow's
  described structure -- not a real captured page. If parsing comes back
  empty against the live site, share a sample of the real
  `/phone/call_logs` HTML (with your own numbers/names redacted) so the
  regexes can be corrected.
- The multi-arch build (basing the Dockerfile on FlareSolverr's own
  published multi-arch image via `build.yaml`, rather than an HA base
  image) is a different pattern than `ohio-aes`/`centerpoint-gas` use --
  worth confirming it actually builds correctly for both `amd64` and
  `aarch64` the first time this add-on is built.

**A known future risk, not a current problem:** community sources report
FlareSolverr is losing effectiveness against Cloudflare's newer Turnstile/
Managed Challenges as Cloudflare's detection evolves. It works against Ooma
today (this add-on is a direct port of a flow that's currently working), but
if Ooma's Cloudflare protection changes in the future and this add-on starts
failing to get past the login page, that's the most likely reason --
alternatives like [Byparr](https://github.com/ThePhaseless/Byparr) exist,
but migrating to one is a real, separate effort, not something built in here
preemptively.

## Notes

- This app is not affiliated with Ooma, Cloudflare, or the FlareSolverr project.
- FlareSolverr is bundled under its MIT license.
- Only tested against a single Ooma account. Not yet run end-to-end against
  the live site -- see "First-run debugging" above.
