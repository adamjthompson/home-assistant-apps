# CenterPoint Energy Gas Usage — Home Assistant App

Logs into your CenterPoint Energy account, reads your natural gas
billing-history table, and imports each meter-read cycle's usage directly
into Home Assistant's long-term statistics, so the **Energy Dashboard**
shows an accurate gas consumption source.

## How it works

CenterPoint's billing-history page shows a table like this:

```
Reading Date   Meter Reading   CCF   Charges
Jul 06,2026    4851            21    $76.89
Jun 03,2026    4830            20    $76.24
May 05,2026    4810            27    $81.41
```

Unlike a typical interval-usage export, `Meter Reading` here is already the
absolute cumulative total in the same unit as `CCF` (`4783 -> 4830 = 47`
matches two months' combined usage, etc.) -- CenterPoint hands over both the
period value and the running total directly, already in CCF, so each row
can be imported as a fully self-contained statistics entry with no local
cumulative-sum ledger and no unit conversion needed.

(An earlier version incorrectly assumed this column was Therms and applied
an estimated Therms-to-CCF conversion -- confirmed wrong via a live run's
own captured table header, which reads "CCF" directly. See CHANGELOG 0.4.0
if you're upgrading from an affected version.)

Meter-read cycles run roughly every 28-33 days, and the read date itself
floats (observed 3rd-7th of the month on this account), so cycle boundaries
are dated to the real scraped `Reading Date` -- never assumed onto the 1st
of the calendar month.

Since CenterPoint only ever gives us one cumulative reading per cycle, each
cycle's usage is spread evenly across the days between reads (an estimate,
logged as such. There's no real information about how usage actually
varied day to day within a cycle). Each day's cumulative total still lands
exactly on the real meter reading at the cycle's actual boundary date, so
weekly/monthly Energy Dashboard views (which just sum whatever's in range)
are accurate either way. This only changes what the *daily* view looks
like, turning one big spike every ~30 days into a continuous (flat, within
each cycle) daily usage line. The oldest available row has no prior reading
to anchor a cycle length against, so it's imported as a single-day entry on
its own reading date instead.

### Login and 2FA

CenterPoint's login can challenge with a verification code, though not on
every login -- the site remembers the device for a while, and successful
runs keep extending that trust window (see below). The full flow, confirmed
end-to-end against live runs:

1. The app persists the browser session (cookies) to
   `/data/centerpoint_state.json` after every successful login. Most runs
   reuse that session and skip login/2FA entirely.
2. If a fresh login is needed, an intermediate "Multi-factor Authentication"
   page asks whether to send the code via **Phone** (pre-selected by
   default) or **Email**. The app selects Email (Phone isn't an option
   here, since only Gmail-based retrieval is implemented).
3. The page that follows doesn't send anything until a separate **Send
   Code** button is clicked -- the app clicks it, waits for the code field
   to become enabled, then reads the verification code directly from a
   Gmail inbox via IMAP, using an app password -- **but only if
   `gmail_address`/`gmail_app_password` are configured.** Both are
   optional; if 2FA is ever challenged with these left blank, the run fails
   with a clear log message telling you to either configure them or log
   into CenterPoint manually once to refresh the remembered-device session,
   rather than crashing confusingly.
4. The code is submitted and the app waits for the login domain to
   actually be left before continuing, since that final verification step
   is asynchronous too.

**Security note:** the Gmail app password grants IMAP read access to the
*entire* mailbox, not just CenterPoint's emails -- there's no way to scope an
app password more narrowly than that. If you'd rather not grant that to your
everyday inbox, a dedicated Gmail account used only for utility/2FA mail is
a stronger isolation option, though that's up to you. If 2FA rarely
triggers for your account, leaving these blank until/unless it actually
happens is a completely reasonable choice.

## Installation

1. In Home Assistant, go to **Settings → Apps → Install App**
2. Click the **⋮** menu → **Repositories**
3. Add this repository URL
4. Find **CenterPoint Energy Gas Usage** and click **Install**

This app requests `homeassistant_api: true`, which lets Supervisor hand it a
scoped token to call Home Assistant's own API -- no manual long-lived access
token needed.

## Configuration

```yaml
centerpoint_username: "your-centerpoint-username"
centerpoint_password: "your-centerpoint-password"
gmail_address: ""
gmail_app_password: ""
cycles_back: 3
run_interval_hours: 24
```

- No meter number or installation ID to configure -- the app lands on the
  account home page and clicks through to usage history the way a real user
  would, rather than needing those account-specific identifiers itself.
- `gmail_address`/`gmail_app_password` are optional -- only needed for
  automatic 2FA-code recovery, which hasn't been observed to trigger yet.
  Leave both blank until/unless you actually see a 2FA-related failure in
  the log. `gmail_app_password` requires 2-Step Verification enabled on the
  Google account to generate (Google Account → Security → App passwords).
- `cycles_back` controls how many recent meter-read cycles are (re-)imported
  each run (as daily-spread entries, not one entry per cycle -- see below).
  The billing-history table shows at most 24 rows and doesn't paginate, so
  anything higher just imports everything available.
- `run_interval_hours` defaults much higher than a typical usage add-on --
  a new row only ever appears once per meter-read cycle (roughly every
  28-33 days), so frequent runs just risk unnecessary logins for no new data.

## Standalone Home Assistant Core usage (no Supervisor)

If you run Home Assistant Core directly (no Supervisor/HAOS -- e.g. a venv
or a plain Docker container without the add-on framework), `core.py` lets
you run this same collector against your Core instance's regular API
instead of installing it as a Supervisor add-on.

```bash
export CENTERPOINT_USERNAME="your-centerpoint-username"
export CENTERPOINT_PASSWORD="your-centerpoint-password"
export HA_URL="http://homeassistant.local:8123"
export HA_TOKEN="your-long-lived-access-token"
python3 centerpoint-gas/core.py
```

- `HA_URL` is your Home Assistant Core instance's base URL.
- `HA_TOKEN` is a long-lived access token (**Profile → Security →
  Long-Lived Access Tokens** in Home Assistant), since there's no Supervisor
  to inject one automatically.
- Every other config option (`gmail_address`, `gmail_app_password`,
  `cycles_back`) is set the same way as the Supervisor add-on -- as an
  environment variable, using the same names.
- Run it on a schedule yourself (cron, systemd timer, etc.) -- there's no
  built-in loop here the way the Supervisor add-on's `run.sh` provides one,
  so `run_interval_hours` doesn't apply to this path.

`core.py` works by pointing the same collector at your Core instance's
`/api` and `/api/websocket` endpoints instead of the Supervisor-proxied
ones. It relies on `centerpoint-gas.py` exposing `SUPERVISOR_TOKEN`/
`SUPERVISOR_API_BASE`/`SUPERVISOR_WS_URL` as simple module-level values --
if that internal structure changes in a future update, `core.py` may need
a matching update.

## Home Assistant statistics

Each run imports gas usage into the external statistic
`centerpoint_gas:cycle_usage_ccf` (in real, metered CCF -- see "How it
works" above). To use it:

1. Go to **Settings → Dashboards → Energy**
2. Under "Gas consumption", add a source and select
   **CenterPoint Gas Usage (CCF)** (`centerpoint_gas:cycle_usage_ccf`)

Because each day's cumulative total is computed fresh from the real,
absolute meter readings each run (not built up from a locally-tracked
running total), re-running with any `cycles_back` value is always safe --
HA overwrites existing entries by date rather than duplicating them, and
there's no equivalent of the "widening `days_back` can corrupt history"
caveat that applies to interval-based statistics. **If you installed a
version before 0.4.0**, the cycles it already imported were inflated by
~3.7% (see CHANGELOG 0.4.0) -- temporarily raise `cycles_back` once to
re-cover and overwrite them with correct values.

## First-run debugging

**The full pipeline is confirmed working end-to-end against the live
site**, including a fresh login through the entire 2FA flow (method
choice, Send Code, email retrieval, code verification) and a reused
remembered-device session on a later run. The 2FA email itself is
confirmed against a real captured message: sent by
`msonlineservicesteam@microsoftonline.com` (Microsoft's own Azure B2C
verification-email service, not a centerpointenergy.com address) with
subject "CenterPoint Energy account email verification code" and a 6-digit
code in the body.

If a run fails, check the log. `_navigate_to_billing_history` and
`scrape_billing_history` both log the actual page text/links on failure
(not saved to a file, just to the container log) specifically so a failure
here is diagnosable without needing a screenshot.

## Notes

- This app is not affiliated with CenterPoint Energy or Google.
- Only tested against a single-meter residential account. The full
  pipeline -- login, the full 2FA flow, navigation, scrape, and statistics
  import -- is confirmed working end-to-end against the live site.
