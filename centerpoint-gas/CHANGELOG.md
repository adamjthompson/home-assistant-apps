# Changelog

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
