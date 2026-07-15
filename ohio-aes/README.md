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
`recorder/import_statistics` WebSocket command as an external statistic
(`ohio_aes:hourly_usage`) -- not published as a plain MQTT sensor. This
matters: a regular sensor state is always timestamped at the moment it's
received, not by any date/time embedded in its payload, so a value that's
lagging a day or two behind (AES's data isn't finalized instantly) would show
up on a chart dated "now" instead of when it actually happened. Statistics
import lets each hour's usage carry its own real timestamp, so history and
the Energy Dashboard are dated correctly no matter how far behind AES's data
finalization runs.

## Installation

1. In Home Assistant, go to **Settings → Apps → Install App**
2. Click the **⋮** menu → **Repositories**
3. Add this repository URL
4. Find **AES Ohio Energy Usage** and click **Install**

This app requests `homeassistant_api: true`, which lets Supervisor hand it a
scoped token to call Home Assistant's own API -- there's nothing to set up
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
There's no separate daily statistic -- Home Assistant aggregates the hourly
data into daily/monthly views on its own -- and no raw 15-minute-resolution
data either, since HA's statistics tables are hourly-resolution regardless
of import mechanism.

**Increasing `days_back` does not backfill the older history it newly
exposes.** The Energy Dashboard needs each hour's cumulative running total,
not just that hour's usage, and there's no reliable prior baseline to build
that total on for hours older than what's already been imported -- doing so
would corrupt every already-imported hour after them. The app detects this
and skips those older hours (logging a warning), rather than risk corrupting
existing history. Re-running with `days_back` back at its old value, or
smaller, is always safe.

AES's export also reliably pads a several-hour mid-day window every day with
a literal `0.00` kWh reading instead of a real value (for this account,
consistently 9:45am-1:45pm) -- a residential meter doesn't actually draw
zero for hours at a stretch, so this is treated as a reporting placeholder,
not real "no usage" data. Runs of 1+ hour of exact-zero readings, bounded by
real readings on both sides, are estimated instead: a smooth curve between
the last real reading before the gap and the first real reading after it,
rather than importing a false zero-usage dip. This is an estimate, not a
measurement -- there's no way to flag it as such once imported, so every
fill is logged (at warning level) with the before/after values it curved
between. Gaps longer than 6 hours are left alone rather than estimated,
since something that long is more likely a genuine outage than the usual
daily placeholder.

Behind the scenes, the app keeps a small ledger at
`/share/ohio_aes_state.json` tracking the cumulative running total the
Energy Dashboard needs (statistics `sum` values must always increase, so HA
can compute period consumption as the delta between them). Re-running the
app, including re-sending the overlapping `days_back` window, is always safe
-- HA overwrites existing hours rather than double-counting them, and a
revised hour's cumulative total correctly carries forward through every later
hour in that run's batch.

## First-run debugging

The login form fields are confirmed against the live site, but the
"Your Energy Use" navigation link and the "Download my data" modal
(date fields, CSV selection, Export button) were mapped from screenshots,
not live selector inspection — they're the most likely spot to need a
small tweak. If a run fails, check the app's log for where it failed and
adjust the corresponding `page.locator`/`page.get_by_...` selector in
`ohio-aes.py`.

If only the statistics-import step fails (the log will say so, but the run
overall won't be marked failed), that's most often a temporary HA API/token
issue -- it retries automatically next `run_interval_hours` cycle and
self-corrects thanks to the overlap window above.

## Notes

- This app is not affiliated with AES Ohio, Opower, or Oracle.
- Only tested against a single-meter residential account.
