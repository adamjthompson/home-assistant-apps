# Ooma Call Logs — Home Assistant App

Logs into your Ooma account, reads your recent call log, and updates a
Home Assistant sensor directly -- no separate Node-RED flow, no
hand-written MQTT template sensor. Ported from a working Node-RED flow; see
"How it works" below for what changed and why.

## How it works

Ooma's customer portal (`my.ooma.com`) sits behind Cloudflare, so a plain
HTTP client (or even a plain headless browser) gets blocked before ever
reaching the login page. This add-on talks to
[FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) -- a proxy that
drives a specially-patched Chromium to solve Cloudflare's challenge -- to
get past that, the same way the original Node-RED flow did.

**This add-on does not bundle or run FlareSolverr itself -- you point it at
an existing FlareSolverr instance you already run** (`flaresolverr_url`).
Bundling it in this same container was tried and reverted: it hit a real,
unresolved upstream "chrome not reachable" bug (see CHANGELOG 0.2.0), and
even setting that aside, bundling risked anyone who already runs
FlareSolverr (as this project's own author does) ending up with two
redundant instances running for no reason.

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

## Prerequisites

**You need a FlareSolverr instance already running somewhere reachable from
Home Assistant** -- this add-on is just a client, not a FlareSolverr
install. If you don't already have one, options include a standalone
`docker run flaresolverr/flaresolverr` on any machine on your network, or a
community Home Assistant add-on that bundles it.

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
flaresolverr_url: "http://192.168.1.30:8191"
run_interval_minutes: 15
```

- `flaresolverr_url` is the base URL of your existing FlareSolverr instance
  (no trailing path needed -- `/v1` is appended automatically).
- `run_interval_minutes` defaults to match the original flow's 15-minute
  cadence, but each run destroys and recreates a FlareSolverr session --
  meaning a full patched-Chromium instance spins up every run on *your*
  FlareSolverr host. That's a real, ongoing CPU/RAM cost (roughly
  300-500MB+ per session), worth tuning down if 15 minutes turns out to be
  too frequent for that host's hardware.

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

**Confirmed via live runs, and why bundling FlareSolverr was abandoned:**
Chromium repeatedly failed inside the bundled container
(`session not created: cannot connect to chrome... chrome not reachable`),
even after fixing an initial root-vs-non-root permission issue. Research
turned up a real, currently-unresolved upstream issue on another community
Home Assistant add-on hitting this exact same error with no confirmed fix
(abandoned, not resolved). Rather than keep chasing an upstream bug with no
known fix, this add-on now depends on a separately-run FlareSolverr instance
instead -- see "Prerequisites" above.

**Still real, unverified pieces**, since this hasn't been run end-to-end
against the live site yet in its current (non-bundled) form:

- The regex-based call-log table parser (`parse_call_logs_html`) is a
  direct, unit-tested port of the original flow's logic, but has only been
  tested against a synthetic HTML sample built to match the original flow's
  described structure -- not a real captured page. If parsing comes back
  empty against the live site, share a sample of the real
  `/phone/call_logs` HTML (with your own numbers/names redacted) so the
  regexes can be corrected.

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
- Only tested against a single Ooma account. Not yet run end-to-end against
  the live site in its current form -- see "First-run debugging" above.
