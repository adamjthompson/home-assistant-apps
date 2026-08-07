# Changelog

## 0.3.5

- Fixed the actual root cause of the "verification-code field not found" failure: the field exists (`{'name': '', 'id': 'verificationCode', 'type': 'text'}`) but its `name` attribute is blank, so the previous `input[name="verificationCode"]` selector could never match it. Switched to `#verificationCode` (by id), matching the same convention already confirmed for `#signInName`/`#password`/`#rememberMe`/`#next`. This page also re-displays a `#signInName` field alongside the code field, now filled defensively (only if present, enabled, and empty) since it's unclear whether it's read-only or a real input. The submit button tries `#next` first (matching the sign-in page's convention) and falls back to the visible "Continue" text.

## 0.3.4

- After selecting Email and clicking Continue on the MFA method-choice page, the page showed a "Please Wait... do not close this window" processing overlay while the code was actually being sent, but the code checked for the verification-code field immediately, before that overlay cleared; the same class of issue as the initial credentials submission (an async JS handler, not a plain page load), just recurring one step later. Now waits for the "Please Wait" text to clear before checking for the code field, falling through to the existing diagnostic dump either way if it doesn't clear in time.

## 0.3.3

- A diagnostic `page.evaluate("() => document.body.innerText...")` call, meant to log page text after a failure, itself crashed with `Cannot read properties of null (reading 'innerText')`, landing in a transient window (likely right after the MFA method-choice page's redirect) where `document.body` didn't exist yet, or the execution context had already been torn down by a navigation. That crash masked whatever the original failure actually was, since it replaced the real error entirely. Added `_safe_body_text`, a null-safe, exception-tolerant wrapper now used everywhere this add-on takes a diagnostic page-text snapshot. It degrades gracefully (returning a clearly-labeled placeholder string) instead of ever crashing itself, since diagnostic code exists to explain a failure, not cause a new one.

## 0.3.2

- Softened `_goto_checked` (added in 0.3.1) from a hard failure to a logged warning on a non-2xx/3xx response: a second live run showed CenterPoint's B2C login redirect can report HTTP 404 on a completely normal, fully-working sign-in page (very likely a client-side-routed SPA quirk where the server 404s the literal path while the SPA's JS still renders it correctly) -- so the raw status code alone isn't a reliable signal on this site, and 0.3.1's hard-fail turned that into a false-positive regression. Content-based checks (`_needs_login`, etc.) remain the real source of truth; the status code is now only logged as a diagnostic breadcrumb.
- Added handling for CenterPoint's real MFA method-choice step, confirmed via a screenshot of a manual login: after credentials are accepted, an intermediate "Multi-factor Authentication" page asks whether to send the code via Phone (pre-selected by default) or Email, before any code is even sent -- a step the previous implementation had no way to know existed. `_login_with_2fa` now detects this page and explicitly selects Email (since only Gmail-based retrieval is implemented), then proceeds to the (still unconfirmed) code-entry step. Also fixed a related bug this uncovered: the existing "wait for leaving the login domain" check would have misdiagnosed a real MFA challenge as a failed login, since the method-choice page never leaves `login.centerpointenergy.com` until after MFA actually completes -- the wait now also resolves on the MFA page's own content, not just a domain change.

## 0.3.1

- CenterPoint's account-home page returned a plain HTTP 404 instead of the expected page, but `_needs_login` only distinguishes "login page" from "not login page", so the 404 sailed through as if authentication had succeeded, and the real failure only surfaced two steps later as a confusing "couldn't find the View Usage link" with no indication anything was actually wrong with the page itself. Added `_goto_checked`, wrapping every `page.goto()` in the login/navigation flow, which now fails immediately with the actual status code and page text logged the moment a navigation returns an error response, whether from a site change on CenterPoint's end or a transient error.

## 0.3.0

- **Breaking change:** switched the imported unit from kWh to CCF (`THERM_TO_CCF = 1.037`, from https://www.paenergyratings.com/resources/natural-gas-units), since CCF is the unit a CenterPoint gas bill actually shows. Unlike the exact therm-to-kWh conversion this replaces, therm-to-CCF is **not exact** -- 1.037 is a commonly-cited industry-average heating value, not this account's real per-cycle conversion factor (which CenterPoint's billing-history table never exposes), so expect the resulting CCF figures to be off by roughly 1-2% from the true metered volume. Flagged clearly in the statistic's own display name ("CenterPoint Gas Usage (CCF, estimated)") as a persistent reminder, not just in these docs. Uses a **new** statistic_id (`centerpoint_gas:cycle_usage_ccf`) rather than changing the unit on the existing `centerpoint_gas:cycle_usage` -- that one already has real kWh history imported, and changing its unit in place risked HA either rejecting the change or silently mixing kWh and CCF values under one ID (CCF values are ~29x smaller than the equivalent kWh, which would look badly wrong on a chart). **Migration:** remove the old kWh source from the Energy Dashboard and add the new CCF one in its place.

## 0.2.1

- `gmail_address`/`gmail_app_password` are now optional (schema `str?`/`password?`) rather than presented as required -- 2FA has never actually triggered in any real run so far, so requiring everyone to hand over Gmail mailbox access for a dormant fallback wasn't justified. If 2FA is ever challenged with these left blank, the run now fails with a clear, actionable log message instead of attempting an IMAP login with empty credentials.
- Replaced the generic, guessed `SUBJECT "verification"` IMAP search with the real confirmed sender and subject, from an actual captured 2FA email: sent by `msonlineservicesteam@microsoftonline.com` (Microsoft's own Azure B2C verification-email service), subject "CenterPoint Energy account email verification code". Verified the quoted-printable HTML body decodes and the 6-digit code extracts correctly against the real email's exact structure. The verification-code entry page/field itself is still unconfirmed, since that requires an actual 2FA challenge to observe.

## 0.2.0

- **First fully working end-to-end run against the live site**, confirmed by the user: login, both usage-history navigation clicks, table scrape, and statistics import all succeeded.
- Changed statistics to spread each cycle's usage evenly across the days between meter reads, instead of dumping the whole cycle's total onto a single day. Previously, since CenterPoint only reports one cumulative reading per ~30-day cycle, the Energy Dashboard's daily view showed one large spike on each reading date and zero on every day in between -- accurate in total, but not useful at daily resolution. Each day's cumulative `sum` still lands exactly on the real meter reading at the cycle's actual boundary (weekly/monthly views, which just sum whatever's in range, are unaffected either way); the per-day split itself is an equal-distribution estimate, logged as such, since there's no real information about how usage actually varied within a cycle. The oldest available row still has no prior reading to anchor a cycle length against, so it's imported as a single-day entry same as before. `cycles_back` now also keeps one extra row beyond the configured window purely as an anchor for the oldest included cycle's spread, dropping that anchor row's own placeholder entry before submitting.

## 0.1.5

- Confirmed the exact real click-through path to usage history (provided directly): from account home, click "View Usage" (-> `/UsageView/Index?ShowMeterInfo=True&ST=Gas`), then "View Historical Energy Usage" (-> the real, fully-populated `/UsageView/UsageHistory?MeterNumber=...&Installation=...` URL). Replaced the single-guess link-candidate list with this confirmed two-step sequence.

## 0.1.4

- Fixed a real bug found via a live run: login now succeeds fully (confirmed "Welcome Adam Thompson!" on the account home page), but navigating directly to the billing-history URL without `MeterNumber`/`Installation` landed on a generic `/Error/Index` page instead of the real table -- those params turn out to only get resolved through the SPA's own client-side account-selection state, which a fresh `page.goto()` bypasses entirely. Replaced the direct URL construction with landing on the account home page (the same place login already redirects to) and clicking through to usage history by visible link text, the way a real user would -- consistent with how `ohio-aes` handles AES's own JS-driven navigation.

## 0.1.3

- Replaced every `wait_for_load_state("networkidle")`/`wait_until="networkidle"` call with `"load"` after a live run showed `networkidle` hanging the full 30s timeout even though the page's `load` event had already fired -- a known Playwright gotcha where persistent background network activity (analytics, heartbeats, etc.) on real-world sites can keep `networkidle` from ever resolving. This was the actual cause of the 0.1.2 fix's own next step timing out.

## 0.1.2

- Fixed a real login bug found via a live run: clicking the sign-in button only triggers an async JS handler (an AJAX POST, not a plain form submit), so `wait_for_load_state("networkidle")` alone could resolve before that round-trip actually finished and redirected away -- the code would then wrongly read "still on the unsubmitted sign-in page" as "login succeeded, no 2FA needed" and proceed to re-request the billing-history page, which just bounced back to login again. Now waits explicitly for the browser to leave the login domain (with a clear error + page-text dump if it doesn't within 30s) before checking for a 2FA prompt.

## 0.1.1

- Removed the `centerpoint_meter_number`/`centerpoint_installation_id` config options -- confirmed the billing-history URL auto-populates both for the logged-in account, so no manual lookup/config is needed at all.

## 0.1.0

- Initial version: Playwright-driven login to CenterPoint Energy (Azure AD B2C -- confirmed real login URL and sign-in field IDs `#signInName`/`#password`/`#rememberMe`/`#next`, with browser-session persistence across runs and a Gmail IMAP fallback for the occasional emailed 2FA code), scraping the billing-history table (Reading Date / Meter Reading / Therms), converting Therms to kWh, and importing each meter-read cycle as an external Home Assistant statistic (`centerpoint_gas:cycle_usage`) ready to add as an Energy Dashboard gas source. Table-scraping still needs a real look at the authenticated page's markup, with debug logging added to `scrape_billing_history` to surface the actual page content on failure. The 2FA-challenge page itself and its email-search pattern remain unconfirmed placeholders -- see the README's "First-run debugging" section.
