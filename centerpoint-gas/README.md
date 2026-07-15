# CenterPoint Energy Gas Usage — Home Assistant App

Logs into your CenterPoint Energy account, reads your natural gas
billing-history table, and imports each meter-read cycle's usage directly
into Home Assistant's long-term statistics, so the **Energy Dashboard**
shows an accurate gas consumption source.

## How it works

CenterPoint's billing-history page shows a table like this:

```
Reading Date   Meter Reading   Therms   Charges
Jul 06,2026    4851            21       $76.89
Jun 03,2026    4830            20       $76.24
May 05,2026    4810            27       $81.41
```

Unlike a typical interval-usage export, `Meter Reading` here is already the
absolute cumulative total in the same unit as `Therms` (`4783 -> 4830 = 47`
matches two months' combined usage, etc.) -- CenterPoint hands over both the
period value and the running total directly, so each row can be imported as
a fully self-contained statistics entry with no local cumulative-sum ledger
needed.

Therms are converted to **kWh** before import: Home Assistant's Energy
Dashboard gas-source unit picker accepts volume units (CCF, ft³, L, MCF, m³)
and energy units (kWh, MWh, GJ, etc.) but not therms directly, so this add-on
does a fixed conversion (`kWh = therms × 29.3001111`, the standard US-therm
conversion factor) rather than passing therms straight through.

Each entry is dated to the real scraped `Reading Date` -- meter-read cycles
run roughly every 28-33 days and the read date itself floats (observed
3rd-7th of the month on this account), so entries are *not* assumed onto the
1st of the calendar month.

### Login and 2FA

CenterPoint's login can challenge with a verification code emailed to you,
though not on every login -- the site appears to remember the device for a
while. To handle this without manual intervention:

1. The app persists the browser session (cookies) to
   `/data/centerpoint_state.json` after every successful login. Most runs
   reuse that session and skip login/2FA entirely.
2. If a fresh login is needed and a 2FA prompt appears, the app reads the
   verification code directly from a Gmail inbox via IMAP, using an app
   password.

**Security note:** the Gmail app password grants IMAP read access to the
*entire* mailbox, not just CenterPoint's emails -- there's no way to scope an
app password more narrowly than that. If you'd rather not grant that to your
everyday inbox, a dedicated Gmail account used only for utility/2FA mail is
a stronger isolation option, though that's up to you.

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
gmail_address: "you@gmail.com"
gmail_app_password: "your-gmail-app-password"
cycles_back: 3
run_interval_hours: 24
```

- No meter number or installation ID to configure -- the app lands on the
  account home page and clicks through to usage history the way a real user
  would, rather than needing those account-specific identifiers itself.
- `gmail_app_password` requires 2-Step Verification enabled on the Google
  account to generate (Google Account → Security → App passwords).
- `cycles_back` controls how many recent meter-read rows are (re-)imported
  each run. The billing-history table shows at most 24 rows and doesn't
  paginate, so anything higher just imports everything available.
- `run_interval_hours` defaults much higher than a typical usage add-on --
  a new row only ever appears once per meter-read cycle (roughly every
  28-33 days), so frequent runs just risk unnecessary logins for no new data.

## Home Assistant statistics

Each run imports gas usage into the external statistic
`centerpoint_gas:cycle_usage` (in kWh). To use it:

1. Go to **Settings → Dashboards → Energy**
2. Under "Gas consumption", add a source and select **CenterPoint Gas Usage**
   (`centerpoint_gas:cycle_usage`)

Because each row is self-contained (real cumulative total, not a
reconstructed one), re-running with any `cycles_back` value is always safe --
HA overwrites existing entries by date rather than duplicating them, and
there's no equivalent of the "widening `days_back` can corrupt history"
caveat that applies to interval-based statistics.

## First-run debugging

The login URL, the sign-in form's field IDs (`#signInName`/`#password`/
`#rememberMe`/`#next`), and the account-home-to-billing-history navigation
(click "View Usage", then "View Historical Energy Usage") are all confirmed
against the real, live CenterPoint site. Login itself is confirmed to
succeed end-to-end, including `#rememberMe` skipping 2FA on a repeat run.
**One thing is still unconfirmed:**

- The 2FA-challenge step itself (`_login_with_2fa`'s check for
  `input[name="verificationCode"]`) and the 2FA email search
  (`_search_gmail_for_code`'s generic `SUBJECT "verification"` match) --
  neither has been exercised yet since this account hasn't hit a 2FA
  challenge in testing so far.

`scrape_billing_history` looks for a `<table>` whose text includes "Reading
Date" and "Therms" rather than a guessed CSS selector, which should be
reasonably resilient to markup details, but also hasn't been reached yet in
testing.

If a run fails, check the log -- both `_navigate_to_billing_history` and
`scrape_billing_history` log the actual page text/links on failure (not
saved to a file, just to the container log) specifically so a failure here
is diagnosable without needing a screenshot.

## Notes

- This app is not affiliated with CenterPoint Energy or Google.
- Only tested against a single-meter residential account. Login is confirmed
  working end-to-end against the live site; the usage-history navigation and
  table scrape are still being bootstrapped against real runs.
