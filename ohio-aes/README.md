# AES Ohio Energy Usage — Home Assistant App

Logs into your AES Ohio account (`myprofile.aes-ohio.com`), downloads your
electricity usage, and imports correctly-dated hourly totals directly into
Home Assistant's long-term statistics, so the **Energy Dashboard** shows
accurate history.

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
interval usage as CSV, and sums it into hourly totals.

Those hourly totals are imported into Home Assistant via the recorder's
`recorder/import_statistics` WebSocket command as an external statistic.
(`ohio_aes:hourly_usage`) -- Statistics
import lets each hour's usage carry its own real timestamp, so history and
the Energy Dashboard are dated correctly no matter how far behind AES's data
finalization runs.

## Installation

1. In Home Assistant, go to **Settings → Apps → Install App**
2. Click the **⋮** menu → **Repositories**
3. Add this repository URL
4. Find **AES Ohio Energy Usage** and click **Install**

This app requests `homeassistant_api: true`, which lets Supervisor hand it a
scoped token to call Home Assistant's own API. There's nothing to set up
for this yourself; no manual long-lived access token needed.

## Configuration

```yaml
aes_username: "your-aes-ohio-username"
aes_password: "your-aes-ohio-password"
days_back: 3
run_interval_hours: 12
```

`days_back` controls how many days of usage history are requested on each
run (a few days of overlap covers AES's data-finalization lag, and lets the
app re-send and correct recently-finalized hours). `run_interval_hours`
controls how often it logs in and re-checks.

## Home Assistant statistics

Each run imports hourly kWh totals into the external statistic
`ohio_aes:hourly_usage`. To use it:

1. Go to **Settings → Dashboards → Energy**
2. Under "Electricity grid", add a consumption source and select
   **AES Ohio Hourly Usage** (`ohio_aes:hourly_usage`)

You can also inspect it directly under **Developer Tools → Statistics**.
There's no separate daily statistic. Home Assistant aggregates the hourly
data into daily/monthly views on its own. There is no raw 15-minute-resolution
data either, since HA's statistics tables are hourly-resolution regardless
of import mechanism.

AES's export also reliably pads a several-hour mid-day window every day with
a literal `0.00` kWh reading instead of a real value (for this account,
consistently 9:45am-1:45pm) -- a residential meter doesn't actually draw
zero for hours at a stretch, so this is treated as a reporting placeholder,
not real "no usage" data. Runs of 1+ hour of exact-zero readings, bounded by
real readings on both sides, are estimated instead: a straight-line ramp
between the average of the last 3 real readings before the gap and the
average of the first 3 real readings after it, rather than importing a false
zero-usage dip. Averaging a few readings on each side (rather than just the
one immediately adjacent to the gap) keeps a single noisy interval, e.g.
an AC compressor cycling on right as reporting resumes, from skewing the
whole ramp. This is an estimate, not a measurement. There's no way to flag
it as such once imported, so every fill is logged (at warning level) with
the before/after values it ramped
between. Gaps longer than 6 hours are left alone rather than estimated,
since something that long is more likely a genuine outage than the usual
daily placeholder.

Behind the scenes, the app keeps a small ledger at
`/share/ohio_aes_state.json` tracking the cumulative running total the
Energy Dashboard needs (statistics `sum` values must always increase, so HA
can compute period consumption as the delta between them). Re-running the
app, including re-sending the overlapping `days_back` window, is always safe. HA overwrites existing hours rather than double-counting them, and a
revised hour's cumulative total correctly carries forward through every later
hour in that run's batch.

## Notes

- This app is not affiliated with AES Ohio, Opower, or Oracle.
- Only tested against a single-meter residential account.
