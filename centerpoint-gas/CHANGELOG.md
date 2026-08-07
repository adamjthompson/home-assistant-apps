# Changelog

## 0.4.1

- Every row failed to parse on the table-scrape step: `_ROW_DATE_FORMAT` assumed "Jul 06,2026" (no space after the comma), but the real scraped text is "Jul 06, 2026" (with a space). Fixed the format string (`"%b %d, %Y"`) -- confirmed directly against the real captured rows in the failing run's own log.

## 0.4.0

- The scraped figures are used as-is now, and are the real metered values, not an estimate. The statistic's display name drops its "(CCF, estimated)" qualifier accordingly (daily-level spreading within a cycle is still an estimate, as always; the cycle totals themselves are not). Also fixed the table-detection check itself to match on `"CCF"` instead of `"Therms"`. **If you're upgrading from an affected version (0.3.0-0.3.10) and already have imported statistics**, temporarily raise `cycles_back` once on your next run so it re-covers however many cycles were imported under the old, inflated logic -- HA's statistics import overwrites existing entries by date rather than duplicating them, so this self-corrects under the same `centerpoint_gas:cycle_usage_ccf` statistic_id with no migration needed.

## 0.3.10

- The full login + 2FA flow is now confirmed working. Now waits explicitly (up to 20s) for each nav-step link to become visible, falling through to the same diagnostic dump only if that times out.

## 0.3.9

- Now waits to actually leave the login domain before returning, same as the credentials-submit and MFA-method-choice steps already do, with a clear error (and the page's own text, which should state directly if the code was wrong) if it doesn't within 30s.

## 0.3.8

- CenterPoint doesn't send anything until a separate, previously-unknown **"Send Code"** button is clicked. The code went straight to polling Gmail every run, for an email CenterPoint had never been asked to send. Now clicks Send Code first and waits for the field to become enabled before fetching from Gmail. Also corrected this page's real submit-button id, `#..._but_verify_code` (class="verifyCode"), replacing the previous unconfirmed `#next` guess.

## 0.3.7

- DOM-confirmed fix for MFA method-choice selection, replacing 0.3.6's hedge. Now checks `#custom_email` first with a normal (non-forced) check, since it's really visible and this fires whatever click/change handler CenterPoint wired to it, then force-checks `#mfaMethod_email` as a redundant safety net. Also switched this page's Continue button from role/text matching to the confirmed real id, `#continue` (`<button id="continue" type="submit" form="attributeVerification">`.

## 0.3.6

- Now tries every known candidate id directly (`force=True`, since one pair may be visually hidden) and logs `#mfaMethod_email`'s actual checked state afterward.

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
