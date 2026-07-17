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

## Dashboard display
To display the call logs on your dashboard, one solution is to use a markdown card. *Note: Requires that `card_mod` is installed also.*

```yaml
type: markdown
icon: mdi:phone-log
content: >-
  {% set calls = state_attr('sensor.ooma_call_feed', 'calls') %}  {% if calls !=
  None and calls | length > 0 %}  <table>  {% for call in calls %}  <tr>  <td
  style="padding: 10px; width: 40%;"><a href="#{{ call.type }}">{{ call.name
  }}</a></td>  <td style="padding: 10px; width: 25%;"><a href="#number">{{
  call.number }}</a></td>  <td style="padding: 10px; width: 20%;">{{ call.date
  }}</td>  <td style="padding: 10px; width: 15%; text-align: right;">{{
  call.duration }}</td>  </tr>  {% endfor %}  </table>  {% else %}  *No recent
  calls found.*  {% endif %}
card_mod:
  style:
    ha-markdown:
      $: |
        /* Force the table to span edge-to-edge */
        table {
          width: 100% !important;
          display: table !important;
          border-collapse: collapse !important;
          border: none !important;
        }
        /* Remove default boxy borders and add a clean bottom divider */
        th, td {
          border: none !important;
          border-bottom: 1px solid rgba(128, 128, 128, 0.2) !important;
        }
        /* Remove the divider from the very last row */
        tr:last-child td {
          border-bottom: none !important;
        }
        /* Style our hidden anchor tags */
        a[href="#Missed"] { 
          color: #ef5350 !important; 
          font-weight: 600 !important; 
          text-decoration: none !important; 
          pointer-events: none !important;
          cursor: default !important;
        }
        a[href="#Incoming"] { 
          color: #66bb6a !important; 
          font-weight: 600 !important; 
          text-decoration: none !important; 
          pointer-events: none !important;
          cursor: default !important;
        }
        a[href="#Outgoing"] { 
          color: #42a5f5 !important; 
          font-weight: 600 !important; 
          text-decoration: none !important; 
          pointer-events: none !important;
          cursor: default !important;
        }
        a[href="#number"] { 
          color: #cccccc !important; 
          text-decoration: none !important; 
          pointer-events: none !important;
          cursor: default !important;
        }
grid_options:
  columns: 12
  rows: auto
```


## Notes
- **A known future risk, not a current problem:** Community sources report
FlareSolverr is losing effectiveness against Cloudflare's newer Turnstile/
Managed Challenges as Cloudflare's detection evolves. It works against Ooma
today (this add-on is a direct port of a flow that's currently working), but
if Ooma's Cloudflare protection changes in the future and this add-on starts
failing to get past the login page, that's the most likely reason --
alternatives like [Byparr](https://github.com/ThePhaseless/Byparr) exist,
but migrating to one is a real, separate effort, not something built in here
preemptively.
- This app is not affiliated with Ooma, Cloudflare, or the FlareSolverr project.
- Only tested against a single Ooma account, with a single FlareSolverr
  instance running elsewhere on the network -- confirmed working end-to-end.
